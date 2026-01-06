from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
import json
import asyncio
from pydantic import BaseModel, Field, EmailStr
from typing import Any, Dict, List, Optional, Tuple
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import bcrypt
import jwt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logger = logging.getLogger(__name__)


leaderboard_lock = asyncio.Lock()
leaderboard_scheduler: Optional[AsyncIOScheduler] = None


class _InMemoryResult:
    def __init__(self, *, matched_count: int = 0, deleted_count: int = 0):
        self.matched_count = matched_count
        self.deleted_count = deleted_count


def _apply_projection(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    if not projection:
        return dict(doc)
    # This codebase only uses exclusion projections like {"_id": 0, "password": 0}
    excluded_keys = {k for k, v in projection.items() if v == 0}
    return {k: v for k, v in doc.items() if k not in excluded_keys}


def _match_filter(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict):
            value = doc.get(key)
            gte = expected.get("$gte")
            lte = expected.get("$lte")
            if gte is not None and (value is None or value < gte):
                return False
            if lte is not None and (value is None or value > lte):
                return False
            continue

        if doc.get(key) != expected:
            return False
    return True


class _InMemoryCursor:
    def __init__(self, docs: List[Dict[str, Any]], projection: Optional[Dict[str, int]]):
        self._docs = docs
        self._projection = projection
        self._sort: Optional[Tuple[str, int]] = None

    def sort(self, field: str, direction: int):
        self._sort = (field, direction)
        return self

    async def to_list(self, length: int) -> List[Dict[str, Any]]:
        docs = list(self._docs)
        if self._sort is not None:
            field, direction = self._sort
            reverse = direction == -1
            docs.sort(key=lambda d: d.get(field), reverse=reverse)

        limited = docs[:length]
        return [_apply_projection(d, self._projection) for d in limited]


class _InMemoryCollection:
    def __init__(self):
        self._docs: List[Dict[str, Any]] = []

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        for doc in self._docs:
            if _match_filter(doc, query):
                return _apply_projection(doc, projection)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self._docs.append(dict(doc))
        return _InMemoryResult(matched_count=1)

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None) -> _InMemoryCursor:
        matched = [d for d in self._docs if _match_filter(d, query)]
        return _InMemoryCursor(matched, projection)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        update_set = update.get("$set")
        if not isinstance(update_set, dict):
            return _InMemoryResult(matched_count=0)

        for doc in self._docs:
            if _match_filter(doc, query):
                doc.update(update_set)
                return _InMemoryResult(matched_count=1)
        return _InMemoryResult(matched_count=0)

    async def delete_one(self, query: Dict[str, Any]):
        for i, doc in enumerate(self._docs):
            if _match_filter(doc, query):
                del self._docs[i]
                return _InMemoryResult(deleted_count=1)
        return _InMemoryResult(deleted_count=0)

    async def delete_many(self, query: Dict[str, Any]):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not _match_filter(d, query)]
        return _InMemoryResult(deleted_count=before - len(self._docs))


class InMemoryDB:
    def __init__(self):
        self.users = _InMemoryCollection()
        self.habits = _InMemoryCollection()
        self.habit_logs = _InMemoryCollection()
        self.weekly_scores = _InMemoryCollection()
        self.weekly_history = _InMemoryCollection()
        self.meta = _InMemoryCollection()


class FileBackedDB:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: Dict[str, List[Dict[str, Any]]] = {
            "users": [],
            "habits": [],
            "habit_logs": [],
            "weekly_scores": [],
            "weekly_history": [],
            "meta": [],
        }
        self._load_from_disk()

        self.users = _FileBackedCollection(self, "users")
        self.habits = _FileBackedCollection(self, "habits")
        self.habit_logs = _FileBackedCollection(self, "habit_logs")
        self.weekly_scores = _FileBackedCollection(self, "weekly_scores")
        self.weekly_history = _FileBackedCollection(self, "weekly_history")
        self.meta = _FileBackedCollection(self, "meta")

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            loaded = json.loads(raw) if raw.strip() else {}
            if isinstance(loaded, dict):
                for key in ("users", "habits", "habit_logs", "weekly_scores", "weekly_history", "meta"):
                    value = loaded.get(key)
                    if isinstance(value, list):
                        self._data[key] = value
        except Exception as e:
            logger.warning("Failed to load file-backed DB (%s). Starting empty.", str(e))

    async def _save_to_disk(self) -> None:
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = json.dumps(self._data, ensure_ascii=False, separators=(",", ":"))
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self._path)


class _FileBackedCollection:
    def __init__(self, db: FileBackedDB, key: str):
        self._db = db
        self._key = key

    def _docs(self) -> List[Dict[str, Any]]:
        return self._db._data[self._key]

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        async with self._db._lock:
            for doc in self._docs():
                if _match_filter(doc, query):
                    return _apply_projection(doc, projection)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        async with self._db._lock:
            self._docs().append(dict(doc))
            await self._db._save_to_disk()
        return _InMemoryResult(matched_count=1)

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None) -> _InMemoryCursor:
        # Cursor is consumed later; keep it independent of future mutations.
        matched = [dict(d) for d in self._docs() if _match_filter(d, query)]
        return _InMemoryCursor(matched, projection)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        update_set = update.get("$set")
        if not isinstance(update_set, dict):
            return _InMemoryResult(matched_count=0)

        async with self._db._lock:
            for doc in self._docs():
                if _match_filter(doc, query):
                    doc.update(update_set)
                    await self._db._save_to_disk()
                    return _InMemoryResult(matched_count=1)
        return _InMemoryResult(matched_count=0)

    async def delete_one(self, query: Dict[str, Any]):
        async with self._db._lock:
            for i, doc in enumerate(self._docs()):
                if _match_filter(doc, query):
                    del self._docs()[i]
                    await self._db._save_to_disk()
                    return _InMemoryResult(deleted_count=1)
        return _InMemoryResult(deleted_count=0)

    async def delete_many(self, query: Dict[str, Any]):
        async with self._db._lock:
            before = len(self._docs())
            self._db._data[self._key] = [d for d in self._docs() if not _match_filter(d, query)]
            deleted = before - len(self._db._data[self._key])
            if deleted:
                await self._db._save_to_disk()
        return _InMemoryResult(deleted_count=deleted)


# Database handle is initialized on startup.
client = None
db: Any = None

# JWT Settings
APP_ENV = os.environ.get("APP_ENV") or os.environ.get("ENV") or "development"
IS_PROD = APP_ENV.lower() in {"prod", "production"}

_DEFAULT_JWT_SECRET = "habit-tracker-secret-key-2024"
_jwt_secret_env = os.environ.get("JWT_SECRET")
JWT_SECRET = _jwt_secret_env or _DEFAULT_JWT_SECRET
JWT_SECRET_SOURCE = "env" if _jwt_secret_env else "default"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

security = HTTPBearer()

# ============== MODELS ==============

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    created_at: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ============== LEADERBOARD MODELS ==============

class LeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    name: str
    score: int
    avatar_url: Optional[str] = None


class WeeklyLeaderboardResponse(BaseModel):
    week_start: str
    week_end: str
    reset_at: str
    timezone: str
    generated_at: str
    limit: int
    offset: int
    entries: List[LeaderboardEntry]
    me: Optional[LeaderboardEntry] = None


class LeaderboardCountdownResponse(BaseModel):
    timezone: str
    now: str
    day_end: str
    week_end: str
    month_end: str
    day_remaining_seconds: int
    week_remaining_seconds: int
    month_remaining_seconds: int


class UpdateScoreRequest(BaseModel):
    user_id: str
    delta: int = Field(..., ge=-1000, le=1000)
    reason: Optional[str] = None


class UpdateScoreResponse(BaseModel):
    user_id: str
    week_start: str
    week_end: str
    score: int
    updated_at: str

class HabitCreate(BaseModel):
    name: str
    category: str  # Health, Study, Work, Fitness, Personal
    frequency: str = "daily"  # daily, weekly
    goal: int = 7  # days per week
    color: str = "#6366F1"

class HabitUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    frequency: Optional[str] = None
    goal: Optional[int] = None
    color: Optional[str] = None
    is_active: Optional[bool] = None

class HabitResponse(BaseModel):
    id: str
    user_id: str
    name: str
    category: str
    frequency: str
    goal: int
    color: str
    is_active: bool
    created_at: str
    current_streak: int = 0
    longest_streak: int = 0

class HabitLogCreate(BaseModel):
    habit_id: str
    date: str  # YYYY-MM-DD format
    status: str  # completed, missed, skipped

class HabitLogResponse(BaseModel):
    id: str
    habit_id: str
    user_id: str
    date: str
    status: str
    created_at: str

# ============== AUTH HELPERS ==============

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await db.users.find_one({"id": user_id}, {"_id": 0, "password": 0})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============== AUTH ROUTES ==============

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    # Check if user exists
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    user_doc = {
        "id": user_id,
        "email": user_data.email,
        "name": user_data.name,
        "password": hash_password(user_data.password),
        "created_at": now
    }
    
    await db.users.insert_one(user_doc)
    
    # Create token
    token = create_access_token(user_id, user_data.email)
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user_id,
            email=user_data.email,
            name=user_data.name,
            created_at=now
        )
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(login_data: UserLogin):
    user = await db.users.find_one({"email": login_data.email})
    if not user or not verify_password(login_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_access_token(user["id"], user["email"])
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user["id"],
            email=user["email"],
            name=user["name"],
            created_at=user["created_at"]
        )
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        created_at=current_user["created_at"]
    )

# ============== HABIT ROUTES ==============

@api_router.post("/habits", response_model=HabitResponse)
async def create_habit(habit_data: HabitCreate, current_user: dict = Depends(get_current_user)):
    habit_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    habit_doc = {
        "id": habit_id,
        "user_id": current_user["id"],
        "name": habit_data.name,
        "category": habit_data.category,
        "frequency": habit_data.frequency,
        "goal": habit_data.goal,
        "color": habit_data.color,
        "is_active": True,
        "created_at": now,
        "current_streak": 0,
        "longest_streak": 0
    }
    
    await db.habits.insert_one(habit_doc)
    
    return HabitResponse(**{k: v for k, v in habit_doc.items() if k != "_id"})

@api_router.get("/habits", response_model=List[HabitResponse])
async def get_habits(current_user: dict = Depends(get_current_user)):
    habits = await db.habits.find(
        {"user_id": current_user["id"]},
        {"_id": 0}
    ).to_list(100)
    
    # Calculate streaks for each habit
    for habit in habits:
        streak_data = await calculate_streak(habit["id"], current_user["id"])
        habit["current_streak"] = streak_data["current_streak"]
        habit["longest_streak"] = streak_data["longest_streak"]
    
    return habits

@api_router.get("/habits/{habit_id}", response_model=HabitResponse)
async def get_habit(habit_id: str, current_user: dict = Depends(get_current_user)):
    habit = await db.habits.find_one(
        {"id": habit_id, "user_id": current_user["id"]},
        {"_id": 0}
    )
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    streak_data = await calculate_streak(habit_id, current_user["id"])
    habit["current_streak"] = streak_data["current_streak"]
    habit["longest_streak"] = streak_data["longest_streak"]
    
    return habit

@api_router.put("/habits/{habit_id}", response_model=HabitResponse)
async def update_habit(habit_id: str, habit_data: HabitUpdate, current_user: dict = Depends(get_current_user)):
    update_data = {k: v for k, v in habit_data.model_dump().items() if v is not None}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    result = await db.habits.update_one(
        {"id": habit_id, "user_id": current_user["id"]},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    habit = await db.habits.find_one({"id": habit_id}, {"_id": 0})
    streak_data = await calculate_streak(habit_id, current_user["id"])
    habit["current_streak"] = streak_data["current_streak"]
    habit["longest_streak"] = streak_data["longest_streak"]
    
    return habit

@api_router.delete("/habits/{habit_id}")
async def delete_habit(habit_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.habits.delete_one({"id": habit_id, "user_id": current_user["id"]})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    # Delete associated logs
    await db.habit_logs.delete_many({"habit_id": habit_id})
    
    return {"message": "Habit deleted successfully"}

# ============== HABIT LOG ROUTES ==============

@api_router.post("/habits/log", response_model=HabitLogResponse)
async def log_habit(log_data: HabitLogCreate, current_user: dict = Depends(get_current_user)):
    # Verify habit exists and belongs to user
    habit = await db.habits.find_one({"id": log_data.habit_id, "user_id": current_user["id"]})
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    # Check if log already exists for this date
    existing_log = await db.habit_logs.find_one({
        "habit_id": log_data.habit_id,
        "date": log_data.date
    })
    
    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    if existing_log:
        old_status = existing_log.get("status")
        # Update existing log
        await db.habit_logs.update_one(
            {"id": existing_log["id"]},
            {"$set": {"status": log_data.status}}
        )

        await _apply_weekly_score_from_habit_log(
            user_id=current_user["id"],
            log_date=log_data.date,
            old_status=old_status,
            new_status=log_data.status,
        )
        return HabitLogResponse(
            id=existing_log["id"],
            habit_id=log_data.habit_id,
            user_id=current_user["id"],
            date=log_data.date,
            status=log_data.status,
            created_at=existing_log["created_at"]
        )
    
    log_doc = {
        "id": log_id,
        "habit_id": log_data.habit_id,
        "user_id": current_user["id"],
        "date": log_data.date,
        "status": log_data.status,
        "created_at": now
    }
    
    await db.habit_logs.insert_one(log_doc)

    await _apply_weekly_score_from_habit_log(
        user_id=current_user["id"],
        log_date=log_data.date,
        old_status=None,
        new_status=log_data.status,
    )
    
    return HabitLogResponse(**{k: v for k, v in log_doc.items() if k != "_id"})

@api_router.get("/habits/{habit_id}/logs", response_model=List[HabitLogResponse])
async def get_habit_logs(
    habit_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    query = {"habit_id": habit_id, "user_id": current_user["id"]}
    
    if start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
    elif start_date:
        query["date"] = {"$gte": start_date}
    elif end_date:
        query["date"] = {"$lte": end_date}
    
    logs = await db.habit_logs.find(query, {"_id": 0}).to_list(1000)
    return logs

@api_router.get("/logs", response_model=List[HabitLogResponse])
async def get_all_logs(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    query = {"user_id": current_user["id"]}
    
    if start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
    elif start_date:
        query["date"] = {"$gte": start_date}
    elif end_date:
        query["date"] = {"$lte": end_date}
    
    logs = await db.habit_logs.find(query, {"_id": 0}).to_list(1000)
    return logs

# ============== ANALYTICS ROUTES ==============

async def calculate_streak(habit_id: str, user_id: str) -> dict:
    logs = await db.habit_logs.find(
        {"habit_id": habit_id, "user_id": user_id, "status": "completed"},
        {"_id": 0}
    ).sort("date", -1).to_list(1000)
    
    if not logs:
        return {"current_streak": 0, "longest_streak": 0}
    
    dates = sorted([log["date"] for log in logs], reverse=True)
    
    # Calculate current streak
    current_streak = 0
    today = datetime.now(timezone.utc).date()
    check_date = today
    
    for i in range(len(dates)):
        date_str = dates[i]
        log_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        if i == 0:
            # First date should be today or yesterday
            diff = (today - log_date).days
            if diff > 1:
                break
            check_date = log_date
            current_streak = 1
        else:
            expected_date = check_date - timedelta(days=1)
            if log_date == expected_date:
                current_streak += 1
                check_date = log_date
            else:
                break
    
    # Calculate longest streak
    longest_streak = 0
    streak = 0
    prev_date = None
    
    for date_str in sorted(dates):
        log_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        if prev_date is None:
            streak = 1
        elif (log_date - prev_date).days == 1:
            streak += 1
        else:
            longest_streak = max(longest_streak, streak)
            streak = 1
        
        prev_date = log_date
    
    longest_streak = max(longest_streak, streak, current_streak)
    
    return {"current_streak": current_streak, "longest_streak": longest_streak}

@api_router.get("/analytics/dashboard")
async def get_dashboard_analytics(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    today = datetime.now(timezone.utc).date()
    
    # Get all habits
    habits = await db.habits.find({"user_id": user_id, "is_active": True}, {"_id": 0}).to_list(100)
    total_habits = len(habits)
    
    # Calculate overall current streak (best streak among all habits)
    max_current_streak = 0
    for habit in habits:
        streak_data = await calculate_streak(habit["id"], user_id)
        max_current_streak = max(max_current_streak, streak_data["current_streak"])
    
    # Get weekly data (last 7 days)
    week_start = (today - timedelta(days=6)).isoformat()
    week_end = today.isoformat()
    
    week_logs = await db.habit_logs.find({
        "user_id": user_id,
        "date": {"$gte": week_start, "$lte": week_end}
    }, {"_id": 0}).to_list(1000)
    
    # Calculate weekly completion percentage
    total_possible = total_habits * 7 if total_habits > 0 else 1
    completed_this_week = sum(1 for log in week_logs if log["status"] == "completed")
    weekly_completion = round((completed_this_week / total_possible) * 100, 1) if total_possible > 0 else 0
    
    # Daily completion data for bar chart (Mon-Sun)
    daily_data = []
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    for i in range(7):
        day = today - timedelta(days=6-i)
        day_str = day.isoformat()
        day_logs = [log for log in week_logs if log["date"] == day_str]
        completed = sum(1 for log in day_logs if log["status"] == "completed")
        daily_data.append({
            "day": day_names[day.weekday()],
            "date": day_str,
            "completed": completed,
            "total": total_habits,
            "percentage": round((completed / total_habits) * 100) if total_habits > 0 else 0
        })
    
    # Weekly performance data for line chart
    weekly_performance = []
    for i in range(7):
        day = today - timedelta(days=6-i)
        day_str = day.isoformat()
        day_logs = [log for log in week_logs if log["date"] == day_str]
        completed = sum(1 for log in day_logs if log["status"] == "completed")
        percentage = round((completed / total_habits) * 100) if total_habits > 0 else 0
        weekly_performance.append({
            "day": day_names[day.weekday()],
            "date": day_str,
            "performance": percentage
        })
    
    # Overall completion rate for donut chart
    all_logs = await db.habit_logs.find({"user_id": user_id}, {"_id": 0}).to_list(10000)
    total_logs = len(all_logs)
    completed_logs = sum(1 for log in all_logs if log["status"] == "completed")
    missed_logs = sum(1 for log in all_logs if log["status"] == "missed")
    skipped_logs = sum(1 for log in all_logs if log["status"] == "skipped")
    
    overall_completion = round((completed_logs / total_logs) * 100, 1) if total_logs > 0 else 0
    
    return {
        "kpis": {
            "current_streak": max_current_streak,
            "total_habits": total_habits,
            "weekly_completion": weekly_completion,
            "overall_completion": overall_completion
        },
        "daily_completion": daily_data,
        "weekly_performance": weekly_performance,
        "completion_breakdown": {
            "completed": completed_logs,
            "missed": missed_logs,
            "skipped": skipped_logs
        }
    }

@api_router.get("/analytics/weekly")
async def get_weekly_analytics(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    habits = await db.habits.find({"user_id": user_id, "is_active": True}, {"_id": 0}).to_list(100)
    
    logs = await db.habit_logs.find({
        "user_id": user_id,
        "date": {"$gte": week_start.isoformat(), "$lte": week_end.isoformat()}
    }, {"_id": 0}).to_list(1000)
    
    # Daily scores
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily_scores = []
    best_day = {"day": "", "score": 0}
    worst_day = {"day": "", "score": 100}
    
    for i in range(7):
        day = week_start + timedelta(days=i)
        if day > today:
            break
        day_str = day.isoformat()
        day_logs = [log for log in logs if log["date"] == day_str]
        completed = sum(1 for log in day_logs if log["status"] == "completed")
        score = round((completed / len(habits)) * 100) if habits else 0
        
        daily_scores.append({
            "day": day_names[i],
            "date": day_str,
            "score": score,
            "completed": completed,
            "total": len(habits)
        })
        
        if score > best_day["score"]:
            best_day = {"day": day_names[i], "score": score}
        if score < worst_day["score"]:
            worst_day = {"day": day_names[i], "score": score}
    
    # Weekly score
    total_completed = sum(1 for log in logs if log["status"] == "completed")
    possible = len(habits) * min(7, (today - week_start).days + 1)
    weekly_score = round((total_completed / possible) * 100) if possible > 0 else 0
    
    return {
        "daily_scores": daily_scores,
        "weekly_score": weekly_score,
        "best_day": best_day,
        "worst_day": worst_day,
        "total_completed": total_completed,
        "total_missed": sum(1 for log in logs if log["status"] == "missed")
    }

@api_router.get("/analytics/monthly")
async def get_monthly_analytics(
    year: Optional[int] = None,
    month: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]
    today = datetime.now(timezone.utc).date()
    
    if not year:
        year = today.year
    if not month:
        month = today.month
    
    # Get first and last day of month
    first_day = datetime(year, month, 1).date()
    if month == 12:
        last_day = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1).date() - timedelta(days=1)
    
    habits = await db.habits.find({"user_id": user_id}, {"_id": 0}).to_list(100)
    
    logs = await db.habit_logs.find({
        "user_id": user_id,
        "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()}
    }, {"_id": 0}).to_list(10000)
    
    # Habit-wise completion
    habit_stats = []
    for habit in habits:
        habit_logs = [log for log in logs if log["habit_id"] == habit["id"]]
        completed = sum(1 for log in habit_logs if log["status"] == "completed")
        days_in_month = (last_day - first_day).days + 1
        actual_days = min(days_in_month, (today - first_day).days + 1) if today.month == month and today.year == year else days_in_month
        
        streak_data = await calculate_streak(habit["id"], user_id)
        
        habit_stats.append({
            "id": habit["id"],
            "name": habit["name"],
            "category": habit["category"],
            "color": habit["color"],
            "completed": completed,
            "total": actual_days,
            "completion_percentage": round((completed / actual_days) * 100) if actual_days > 0 else 0,
            "longest_streak": streak_data["longest_streak"]
        })
    
    # Missed days count
    missed_count = sum(1 for log in logs if log["status"] == "missed")
    
    return {
        "year": year,
        "month": month,
        "habit_stats": habit_stats,
        "total_completed": sum(1 for log in logs if log["status"] == "completed"),
        "missed_days_count": missed_count,
        "overall_completion": round((sum(h["completion_percentage"] for h in habit_stats) / len(habit_stats))) if habit_stats else 0
    }

@api_router.get("/analytics/yearly")
async def get_yearly_analytics(
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]
    today = datetime.now(timezone.utc).date()
    
    if not year:
        year = today.year
    
    habits = await db.habits.find({"user_id": user_id}, {"_id": 0}).to_list(100)
    
    # Get all logs for the year
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    
    logs = await db.habit_logs.find({
        "user_id": user_id,
        "date": {"$gte": year_start, "$lte": year_end}
    }, {"_id": 0}).to_list(50000)
    
    # Month-wise completion
    monthly_data = []
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    best_month = {"month": "", "percentage": 0}
    
    for m in range(1, 13):
        month_start = f"{year}-{m:02d}-01"
        if m == 12:
            month_end = f"{year}-12-31"
        else:
            month_end = f"{year}-{m+1:02d}-01"
        
        month_logs = [log for log in logs if month_start <= log["date"] < month_end]
        completed = sum(1 for log in month_logs if log["status"] == "completed")
        
        # Calculate days in month
        if m == 12:
            days = 31
        else:
            days = (datetime(year, m + 1, 1) - datetime(year, m, 1)).days
        
        possible = len(habits) * days if habits else 1
        percentage = round((completed / possible) * 100) if possible > 0 else 0
        
        monthly_data.append({
            "month": month_names[m - 1],
            "month_num": m,
            "completed": completed,
            "percentage": percentage
        })
        
        if percentage > best_month["percentage"]:
            best_month = {"month": month_names[m - 1], "percentage": percentage}
    
    # Heatmap data (daily completion count)
    heatmap_data = []
    current = datetime(year, 1, 1).date()
    end = min(datetime(year, 12, 31).date(), today)
    
    while current <= end:
        day_str = current.isoformat()
        day_logs = [log for log in logs if log["date"] == day_str]
        completed = sum(1 for log in day_logs if log["status"] == "completed")
        total = len(habits)
        
        heatmap_data.append({
            "date": day_str,
            "value": completed,
            "total": total,
            "level": min(4, int((completed / total) * 4)) if total > 0 else 0
        })
        current += timedelta(days=1)
    
    # Top habit of the year
    habit_completions = {}
    for log in logs:
        if log["status"] == "completed":
            habit_id = log["habit_id"]
            habit_completions[habit_id] = habit_completions.get(habit_id, 0) + 1
    
    top_habit = None
    if habit_completions:
        top_habit_id = max(habit_completions, key=habit_completions.get)
        top_habit_data = next((h for h in habits if h["id"] == top_habit_id), None)
        if top_habit_data:
            top_habit = {
                "name": top_habit_data["name"],
                "completions": habit_completions[top_habit_id]
            }
    
    # Overall productivity score
    total_completed = sum(1 for log in logs if log["status"] == "completed")
    total_logged = len(logs)
    productivity_score = round((total_completed / total_logged) * 100) if total_logged > 0 else 0
    
    return {
        "year": year,
        "monthly_data": monthly_data,
        "heatmap_data": heatmap_data,
        "top_habit": top_habit,
        "best_month": best_month,
        "productivity_score": productivity_score,
        "total_completed": total_completed,
        "total_missed": sum(1 for log in logs if log["status"] == "missed")
    }


# ============== LEADERBOARD HELPERS ==============

LEADERBOARD_TZ = os.environ.get("LEADERBOARD_TZ", "UTC")


def _get_leaderboard_tz() -> ZoneInfo:
    try:
        return ZoneInfo(LEADERBOARD_TZ or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _now_lb() -> datetime:
    return datetime.now(_get_leaderboard_tz())


def _week_bounds(dt: datetime) -> tuple[datetime, datetime]:
    # Week: Monday 00:00 -> Sunday 23:59 (inclusive) in leaderboard timezone.
    tz = _get_leaderboard_tz()
    dt = dt.astimezone(tz)
    week_start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end_date = (week_start + timedelta(days=6)).date()
    week_end = datetime(week_end_date.year, week_end_date.month, week_end_date.day, 23, 59, 0, tzinfo=tz)
    return week_start, week_end


def _day_end(dt: datetime) -> datetime:
    dt = dt.astimezone(_get_leaderboard_tz())
    return dt.replace(hour=23, minute=59, second=0, microsecond=0)


def _month_end(dt: datetime) -> datetime:
    tz = _get_leaderboard_tz()
    dt = dt.astimezone(tz)
    year, month = dt.year, dt.month
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=tz)
    last_day = (next_month - timedelta(days=1)).date()
    return datetime(last_day.year, last_day.month, last_day.day, 23, 59, 0, tzinfo=tz)


def _points_for_status(status_value: Optional[str]) -> int:
    # Modular scoring: each completed daily goal (habit completion) is +10 points.
    return 10 if status_value == "completed" else 0


def _streak_bonus_delta(*, old_status: Optional[str], new_status: str) -> int:
    # Modular hook: can be expanded later.
    return 0


async def _get_or_init_leaderboard_state() -> Dict[str, Any]:
    state = await db.meta.find_one({"id": "leaderboard_state"}, {"_id": 0})
    if state:
        return state

    now = _now_lb()
    week_start, week_end = _week_bounds(now)
    now_utc = datetime.now(timezone.utc).isoformat()

    state = {
        "id": "leaderboard_state",
        "timezone": LEADERBOARD_TZ or "UTC",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "last_archived_week_end": None,
        "created_at": now_utc,
        "updated_at": now_utc,
    }
    await db.meta.insert_one(state)
    return {k: v for k, v in state.items() if k != "_id"}


async def _set_leaderboard_state(update: Dict[str, Any]) -> Dict[str, Any]:
    await db.meta.update_one({"id": "leaderboard_state"}, {"$set": update})
    state = await db.meta.find_one({"id": "leaderboard_state"}, {"_id": 0})
    return state or update


async def _ensure_current_week() -> Dict[str, Any]:
    # Fallback if scheduler missed: rollover when needed on request.
    async with leaderboard_lock:
        state = await _get_or_init_leaderboard_state()
        now = _now_lb()

        try:
            state_week_start = datetime.fromisoformat(state["week_start"]).astimezone(_get_leaderboard_tz())
            state_week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())
        except Exception:
            state_week_start, state_week_end = _week_bounds(now)

        if now >= state_week_end:
            await _run_weekly_reset_locked(state_week_start, state_week_end)
            state = await _get_or_init_leaderboard_state()

        return state


async def _run_weekly_reset_locked(week_start: datetime, week_end: datetime) -> None:
    # Assumes leaderboard_lock is held.
    state = await _get_or_init_leaderboard_state()
    if state.get("last_archived_week_end") == week_end.isoformat():
        logger.info("Leaderboard reset already completed for %s", week_end.isoformat())
        return

    reset_at_utc = datetime.now(timezone.utc).isoformat()

    users = await db.users.find({}, {"_id": 0, "password": 0}).to_list(100000)
    scores = await db.weekly_scores.find(
        {"week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
        {"_id": 0},
    ).to_list(100000)
    score_by_user = {s.get("user_id"): int(s.get("score", 0)) for s in scores if s.get("user_id")}

    # Archive for all users (including zero-score users)
    for u in users:
        user_id = u.get("id")
        if not user_id:
            continue
        history_doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "name": u.get("name"),
            "avatar_url": u.get("avatar_url"),
            "score": score_by_user.get(user_id, 0),
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "archived_at": reset_at_utc,
        }
        await db.weekly_history.insert_one(history_doc)

    await db.weekly_scores.delete_many({"week_start": week_start.isoformat(), "week_end": week_end.isoformat()})

    # Initialize next week (Monday 00:00 right after reset)
    next_anchor = (week_end + timedelta(minutes=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    new_week_start, new_week_end = _week_bounds(next_anchor)

    for u in users:
        user_id = u.get("id")
        if not user_id:
            continue
        weekly_doc = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "score": 0,
            "week_start": new_week_start.isoformat(),
            "week_end": new_week_end.isoformat(),
            "updated_at": reset_at_utc,
        }
        await db.weekly_scores.insert_one(weekly_doc)

    await _set_leaderboard_state(
        {
            "timezone": LEADERBOARD_TZ or "UTC",
            "week_start": new_week_start.isoformat(),
            "week_end": new_week_end.isoformat(),
            "last_archived_week_end": week_end.isoformat(),
            "updated_at": reset_at_utc,
        }
    )

    logger.info(
        "Weekly leaderboard reset complete. Archived %s..%s, new week %s..%s",
        week_start.isoformat(),
        week_end.isoformat(),
        new_week_start.isoformat(),
        new_week_end.isoformat(),
    )


async def _apply_weekly_score_from_habit_log(
    *,
    user_id: str,
    log_date: str,
    old_status: Optional[str],
    new_status: str,
) -> None:
    state = await _ensure_current_week()

    try:
        week_start = datetime.fromisoformat(state["week_start"]).astimezone(_get_leaderboard_tz())
        week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())
    except Exception:
        now = _now_lb()
        week_start, week_end = _week_bounds(now)

    week_start_date = week_start.date().isoformat()
    week_end_date = week_end.date().isoformat()
    if not (week_start_date <= log_date <= week_end_date):
        return

    delta = _points_for_status(new_status) - _points_for_status(old_status)
    delta += _streak_bonus_delta(old_status=old_status, new_status=new_status)
    if delta == 0:
        return

    async with leaderboard_lock:
        existing = await db.weekly_scores.find_one(
            {"user_id": user_id, "week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
            {"_id": 0},
        )
        now_utc = datetime.now(timezone.utc).isoformat()

        if not existing:
            new_score = max(0, delta)
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "score": new_score,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "updated_at": now_utc,
            }
            await db.weekly_scores.insert_one(doc)
            logger.info("Weekly score init: user=%s score=%s", user_id, new_score)
            return

        current_score = int(existing.get("score", 0))
        new_score = max(0, current_score + delta)
        await db.weekly_scores.update_one(
            {"id": existing["id"]},
            {"$set": {"score": new_score, "updated_at": now_utc}},
        )
        logger.info("Weekly score update: user=%s delta=%s score=%s", user_id, delta, new_score)


def _require_internal_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> None:
    expected = os.environ.get("LEADERBOARD_INTERNAL_TOKEN")
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal token not configured")
    if credentials.credentials != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _scheduled_weekly_reset() -> None:
    async with leaderboard_lock:
        state = await _get_or_init_leaderboard_state()
        try:
            week_start = datetime.fromisoformat(state["week_start"]).astimezone(_get_leaderboard_tz())
            week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())
        except Exception:
            now = _now_lb()
            week_start, week_end = _week_bounds(now)
        await _run_weekly_reset_locked(week_start, week_end)


# ============== LEADERBOARD ROUTES ==============

@api_router.get("/leaderboard/weekly", response_model=WeeklyLeaderboardResponse)
async def get_weekly_leaderboard(
    limit: int = 10,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    limit = max(1, min(100, limit))
    offset = max(0, offset)

    state = await _ensure_current_week()
    week_start = datetime.fromisoformat(state["week_start"]).astimezone(_get_leaderboard_tz())
    week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())

    users = await db.users.find({}, {"_id": 0, "password": 0}).to_list(100000)
    scores = await db.weekly_scores.find(
        {"week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
        {"_id": 0},
    ).to_list(100000)
    score_by_user = {s.get("user_id"): int(s.get("score", 0)) for s in scores if s.get("user_id")}

    rows: List[Dict[str, Any]] = []
    for u in users:
        user_id = u.get("id")
        if not user_id:
            continue
        rows.append(
            {
                "user_id": user_id,
                "name": u.get("name") or "User",
                "avatar_url": u.get("avatar_url"),
                "score": score_by_user.get(user_id, 0),
            }
        )

    # Tie-breaker: name then id for stability.
    rows.sort(key=lambda r: (-r["score"], (r["name"] or "").lower(), r["user_id"]))

    entries_all: List[LeaderboardEntry] = []
    for idx, r in enumerate(rows):
        entries_all.append(
            LeaderboardEntry(
                rank=idx + 1,
                user_id=r["user_id"],
                name=r["name"],
                score=r["score"],
                avatar_url=r.get("avatar_url"),
            )
        )

    me_entry = next((e for e in entries_all if e.user_id == current_user.get("id")), None)
    paged = entries_all[offset : offset + limit]

    return WeeklyLeaderboardResponse(
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        reset_at=week_end.isoformat(),
        timezone=LEADERBOARD_TZ or "UTC",
        generated_at=datetime.now(timezone.utc).isoformat(),
        limit=limit,
        offset=offset,
        entries=paged,
        me=me_entry,
    )


@api_router.get("/leaderboard/countdown", response_model=LeaderboardCountdownResponse)
async def get_leaderboard_countdown(current_user: dict = Depends(get_current_user)):
    state = await _ensure_current_week()
    now = _now_lb()

    week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())
    day_end = _day_end(now)
    month_end = _month_end(now)

    def remaining_seconds(target: datetime) -> int:
        seconds = int((target - now).total_seconds())
        return max(0, seconds)

    return LeaderboardCountdownResponse(
        timezone=LEADERBOARD_TZ or "UTC",
        now=now.isoformat(),
        day_end=day_end.isoformat(),
        week_end=week_end.isoformat(),
        month_end=month_end.isoformat(),
        day_remaining_seconds=remaining_seconds(day_end),
        week_remaining_seconds=remaining_seconds(week_end),
        month_remaining_seconds=remaining_seconds(month_end),
    )


@api_router.post("/leaderboard/updateScore", response_model=UpdateScoreResponse)
async def update_score_internal(payload: UpdateScoreRequest, _: None = Depends(_require_internal_token)):
    # Internal-only endpoint. Do not expose LEADERBOARD_INTERNAL_TOKEN to clients.
    state = await _ensure_current_week()
    week_start = datetime.fromisoformat(state["week_start"]).astimezone(_get_leaderboard_tz())
    week_end = datetime.fromisoformat(state["week_end"]).astimezone(_get_leaderboard_tz())
    now_utc = datetime.now(timezone.utc).isoformat()

    async with leaderboard_lock:
        existing = await db.weekly_scores.find_one(
            {"user_id": payload.user_id, "week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
            {"_id": 0},
        )

        if not existing:
            new_score = max(0, payload.delta)
            doc = {
                "id": str(uuid.uuid4()),
                "user_id": payload.user_id,
                "score": new_score,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "updated_at": now_utc,
            }
            await db.weekly_scores.insert_one(doc)
            return UpdateScoreResponse(
                user_id=payload.user_id,
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                score=new_score,
                updated_at=now_utc,
            )

        current_score = int(existing.get("score", 0))
        new_score = max(0, current_score + payload.delta)
        await db.weekly_scores.update_one(
            {"id": existing["id"]},
            {"$set": {"score": new_score, "updated_at": now_utc}},
        )
        return UpdateScoreResponse(
            user_id=payload.user_id,
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            score=new_score,
            updated_at=now_utc,
        )


@api_router.get("/leaderboard/history")
async def get_leaderboard_history(
    limit: int = 10,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    items = await db.weekly_history.find({"user_id": current_user["id"]}, {"_id": 0}).to_list(100000)
    items.sort(key=lambda d: d.get("week_end") or "", reverse=True)
    return {
        "limit": limit,
        "offset": offset,
        "entries": items[offset : offset + limit],
    }

# ============== BASIC ROUTES ==============

@api_router.get("/")
async def root():
    return {"message": "FocusFlow Habit Tracker API"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy"}

# Include the router in the main app
app.include_router(api_router)

cors_origins_env = os.environ.get('CORS_ORIGINS', '*')
cors_origins = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
if not cors_origins:
    cors_origins = ['*']
cors_allow_all = len(cors_origins) == 1 and cors_origins[0] == '*'

app.add_middleware(
    CORSMiddleware,
    # Avoid using '*' with credentials. In production, set CORS_ORIGINS to your frontend URL(s).
    allow_credentials=not cors_allow_all,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup_db_client():
    global client, db, leaderboard_scheduler

    if IS_PROD and JWT_SECRET_SOURCE == "default":
        raise RuntimeError("JWT_SECRET must be set in production (refusing to start with default secret).")
    if JWT_SECRET_SOURCE == "default":
        logger.warning("JWT_SECRET not set; using insecure default. Set JWT_SECRET for persistent logins and security.")

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "habit_tracker")

    if mongo_url:
        try:
            client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
            await client.admin.command("ping")
            db = client[db_name]
            logger.info("Connected to MongoDB: %s / %s", mongo_url, db_name)
        except Exception as e:
            logger.warning("MongoDB not available (%s). Falling back to file-backed DB.", str(e))
            client = None

    if db is None:
        data_file = os.environ.get("DATA_FILE")
        path = Path(data_file) if data_file else (ROOT_DIR / "data" / "db.json")
        db = FileBackedDB(path)
        logger.warning("Using file-backed DB at %s (data persists between restarts).", str(path))

    # Ensure leaderboard state exists and schedule weekly reset.
    await _get_or_init_leaderboard_state()
    if leaderboard_scheduler is None:
        tz = _get_leaderboard_tz()
        leaderboard_scheduler = AsyncIOScheduler(timezone=tz)
        leaderboard_scheduler.add_job(
            _scheduled_weekly_reset,
            CronTrigger(day_of_week="sun", hour=23, minute=59, timezone=tz),
            id="weekly_leaderboard_reset",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        leaderboard_scheduler.start()
        logger.info("Leaderboard scheduler started (weekly reset: Sunday 23:59 %s)", LEADERBOARD_TZ or "UTC")

@app.on_event("shutdown")
async def shutdown_db_client():
    global leaderboard_scheduler
    if leaderboard_scheduler is not None:
        try:
            leaderboard_scheduler.shutdown(wait=False)
        except Exception:
            pass
        leaderboard_scheduler = None
    if client is not None:
        client.close()
