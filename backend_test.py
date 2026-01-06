import requests
import sys
import json
from datetime import datetime, timedelta
import os

class HabitTrackerAPITester:
    def __init__(self, base_url=None):
        # Default to local dev server; can be overridden with BACKEND_URL env var.
        self.base_url = (base_url or os.environ.get("BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.token = None
        self.user_id = None
        self.tests_run = 0
        self.tests_passed = 0
        self.created_habit_id = None

    def run_test(self, name, method, endpoint, expected_status, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.base_url}/api/{endpoint}"
        test_headers = {'Content-Type': 'application/json'}
        
        if self.token:
            test_headers['Authorization'] = f'Bearer {self.token}'
        
        if headers:
            test_headers.update(headers)

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=test_headers, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=test_headers, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=test_headers, timeout=10)

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    return True, response.json()
                except:
                    return True, {}
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                try:
                    error_detail = response.json()
                    print(f"   Error: {error_detail}")
                except:
                    print(f"   Response: {response.text}")
                return False, {}

        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_health_check(self):
        """Test basic health endpoint"""
        return self.run_test("Health Check", "GET", "health", 200)

    def test_register(self):
        """Test user registration"""
        timestamp = datetime.now().strftime('%H%M%S')
        user_data = {
            "name": f"Test User {timestamp}",
            "email": f"test{timestamp}@example.com",
            "password": "testpass123"
        }
        
        success, response = self.run_test(
            "User Registration",
            "POST",
            "auth/register",
            200,
            data=user_data
        )
        
        if success and 'access_token' in response:
            self.token = response['access_token']
            self.user_id = response['user']['id']
            print(f"   Token obtained: {self.token[:20]}...")
            return True
        return False

    def test_login(self):
        """Test user login with demo credentials"""
        login_data = {
            "email": "demo@test.com",
            "password": "demo123"
        }
        
        success, response = self.run_test(
            "User Login",
            "POST",
            "auth/login",
            200,
            data=login_data
        )
        
        if success and 'access_token' in response:
            self.token = response['access_token']
            self.user_id = response['user']['id']
            print(f"   Token obtained: {self.token[:20]}...")
            return True
        return False

    def test_get_me(self):
        """Test get current user"""
        return self.run_test("Get Current User", "GET", "auth/me", 200)

    def test_create_habit(self):
        """Test habit creation"""
        habit_data = {
            "name": "Test Habit",
            "category": "Health",
            "frequency": "daily",
            "goal": 7,
            "color": "#6366F1"
        }
        
        success, response = self.run_test(
            "Create Habit",
            "POST",
            "habits",
            200,
            data=habit_data
        )
        
        if success and 'id' in response:
            self.created_habit_id = response['id']
            print(f"   Created habit ID: {self.created_habit_id}")
            return True
        return False

    def test_get_habits(self):
        """Test get all habits"""
        return self.run_test("Get All Habits", "GET", "habits", 200)

    def test_get_habit_by_id(self):
        """Test get specific habit"""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False
        
        return self.run_test(
            "Get Habit by ID",
            "GET",
            f"habits/{self.created_habit_id}",
            200
        )

    def test_update_habit(self):
        """Test habit update"""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False
        
        update_data = {
            "name": "Updated Test Habit",
            "color": "#10B981"
        }
        
        return self.run_test(
            "Update Habit",
            "PUT",
            f"habits/{self.created_habit_id}",
            200,
            data=update_data
        )

    def test_log_habit(self):
        """Test habit logging"""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False
        
        today = datetime.now().strftime('%Y-%m-%d')
        log_data = {
            "habit_id": self.created_habit_id,
            "date": today,
            "status": "completed"
        }
        
        return self.run_test(
            "Log Habit",
            "POST",
            "habits/log",
            200,
            data=log_data
        )

    def test_get_habit_logs(self):
        """Test get habit logs"""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False
        
        return self.run_test(
            "Get Habit Logs",
            "GET",
            f"habits/{self.created_habit_id}/logs",
            200
        )

    def test_get_all_logs(self):
        """Test get all user logs"""
        return self.run_test("Get All Logs", "GET", "logs", 200)

    def test_dashboard_analytics(self):
        """Test dashboard analytics"""
        return self.run_test("Dashboard Analytics", "GET", "analytics/dashboard", 200)

    def test_weekly_analytics(self):
        """Test weekly analytics"""
        return self.run_test("Weekly Analytics", "GET", "analytics/weekly", 200)

    def test_monthly_analytics(self):
        """Test monthly analytics"""
        return self.run_test("Monthly Analytics", "GET", "analytics/monthly", 200)

    def test_yearly_analytics(self):
        """Test yearly analytics"""
        return self.run_test("Yearly Analytics", "GET", "analytics/yearly", 200)

    def test_weekly_leaderboard(self):
        """Test weekly leaderboard endpoint"""
        success, response = self.run_test("Weekly Leaderboard", "GET", "leaderboard/weekly?limit=10&offset=0", 200)
        if not success:
            return False
        if not isinstance(response.get("entries"), list):
            print("❌ Leaderboard entries missing/invalid")
            return False
        if "week_start" not in response or "week_end" not in response:
            print("❌ Leaderboard week window missing")
            return False
        return True

    def test_leaderboard_countdown(self):
        """Test leaderboard countdown endpoint"""
        success, response = self.run_test("Leaderboard Countdown", "GET", "leaderboard/countdown", 200)
        if not success:
            return False
        for key in ("day_remaining_seconds", "week_remaining_seconds", "month_remaining_seconds"):
            if not isinstance(response.get(key), int):
                print(f"❌ Countdown missing/invalid field: {key}")
                return False
        return True

    def test_leaderboard_score_updates_from_logs(self):
        """Verify score changes when habit log status changes (server-side, no client tampering)."""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False

        today = datetime.now().strftime('%Y-%m-%d')

        # Ensure completed
        ok, _ = self.run_test(
            "Log Habit (Completed for Leaderboard)",
            "POST",
            "habits/log",
            200,
            data={"habit_id": self.created_habit_id, "date": today, "status": "completed"},
        )
        if not ok:
            return False

        ok, lb = self.run_test("Weekly Leaderboard (After Completed)", "GET", "leaderboard/weekly?limit=10&offset=0", 200)
        if not ok:
            return False
        me = lb.get("me") or {}
        if int(me.get("score") or 0) < 10:
            print(f"❌ Expected score >= 10 after completion, got {me.get('score')}")
            return False

        # Toggle to missed (should remove the +10)
        ok, _ = self.run_test(
            "Log Habit (Missed for Leaderboard)",
            "POST",
            "habits/log",
            200,
            data={"habit_id": self.created_habit_id, "date": today, "status": "missed"},
        )
        if not ok:
            return False

        ok, lb2 = self.run_test("Weekly Leaderboard (After Missed)", "GET", "leaderboard/weekly?limit=10&offset=0", 200)
        if not ok:
            return False
        me2 = lb2.get("me") or {}
        if int(me2.get("score") or 0) != 0:
            print(f"❌ Expected score 0 after toggling to missed, got {me2.get('score')}")
            return False

        return True

    def test_delete_habit(self):
        """Test habit deletion"""
        if not self.created_habit_id:
            print("❌ No habit ID available for testing")
            return False
        
        return self.run_test(
            "Delete Habit",
            "DELETE",
            f"habits/{self.created_habit_id}",
            200
        )

def main():
    print("🚀 Starting Habit Tracker API Tests")
    print("=" * 50)
    
    tester = HabitTrackerAPITester()
    
    # Test sequence
    tests = [
        ("Health Check", tester.test_health_check),
        ("User Registration", tester.test_register),
        ("Get Current User", tester.test_get_me),
        ("Create Habit", tester.test_create_habit),
        ("Get All Habits", tester.test_get_habits),
        ("Get Habit by ID", tester.test_get_habit_by_id),
        ("Update Habit", tester.test_update_habit),
        ("Log Habit", tester.test_log_habit),
        ("Get Habit Logs", tester.test_get_habit_logs),
        ("Get All Logs", tester.test_get_all_logs),
        ("Dashboard Analytics", tester.test_dashboard_analytics),
        ("Weekly Analytics", tester.test_weekly_analytics),
        ("Monthly Analytics", tester.test_monthly_analytics),
        ("Yearly Analytics", tester.test_yearly_analytics),
        ("Weekly Leaderboard", tester.test_weekly_leaderboard),
        ("Leaderboard Countdown", tester.test_leaderboard_countdown),
        ("Leaderboard Score Updates", tester.test_leaderboard_score_updates_from_logs),
        ("Delete Habit", tester.test_delete_habit),
    ]
    
    # Run all tests
    for test_name, test_func in tests:
        try:
            test_func()
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {str(e)}")
    
    # Print summary
    print("\n" + "=" * 50)
    print(f"📊 Test Results: {tester.tests_passed}/{tester.tests_run} passed")
    success_rate = (tester.tests_passed / tester.tests_run * 100) if tester.tests_run > 0 else 0
    print(f"📈 Success Rate: {success_rate:.1f}%")
    
    if success_rate < 80:
        print("⚠️  Warning: Low success rate detected")
        return 1
    elif success_rate == 100:
        print("🎉 All tests passed!")
        return 0
    else:
        print("✅ Most tests passed")
        return 0

if __name__ == "__main__":
    sys.exit(main())