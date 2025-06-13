import csv
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv
import os
import requests
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import concurrent.futures

# Load environment variables
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- User Loading ---
def load_users():
    """Loads user credentials from environment variables, supporting non-sequential numbering."""
    users = []
    # Collect all environment variable keys
    env_keys = os.environ.keys()
    # Find all SPADA_USERNAME_X patterns
    indices = set()
    for key in env_keys:
        if key.startswith("SPADA_USERNAME_"):
            try:
                idx = int(key.split("_")[-1])
                indices.add(idx)
            except ValueError:
                continue
    for i in sorted(indices):
        username = os.getenv(f"SPADA_USERNAME_{i}")
        password = os.getenv(f"SPADA_PASSWORD_{i}")
        chat_id = os.getenv(f"TELEGRAM_CHAT_ID_{i}")
        if all([username, password, chat_id]):
            users.append({"username": username, "password": password, "chat_id": chat_id})
    return users

# --- Telegram Bot ---
def send_telegram(message, chat_id):
    """Sends a message to a specific Telegram chat."""
    if not TELEGRAM_TOKEN or not chat_id:
        print("Telegram token or chat ID is missing. Skipping notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Failed to send Telegram message to {chat_id}: {e}")


# --- Schedule and Class Logic (Unchanged) ---
def load_schedule(csv_file):
    with open(csv_file, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def get_current_class(schedule):
    now = datetime.now()
    day_map = {
        "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
        "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"
    }
    today = day_map[now.strftime("%A")]
    current_time = now.strftime("%H:%M")

    for entry in schedule:
        if entry["Day"] == today:
            start_time, end_time = entry["Time"].split(" - ")
            if start_time <= current_time <= end_time:
                return entry["CourseName"]
    return None

# --- Browser Automation ---
def init_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless")
        options.add_argument("--window-size=1920,1200")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)

def login_and_attend(user, course_name):
    """Main automation logic for a single user."""
    driver = init_driver()
    username = user["username"]
    password = user["password"]
    chat_id = user["chat_id"]

    print(f"Processing attendance for {username}...")

    try:
        driver.get("https://spada.upnyk.ac.id/login/index.php")

        # Wait for the login form to be present and interactable
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.ID, "username")))
        wait.until(EC.presence_of_element_located((By.ID, "password")))
        wait.until(EC.element_to_be_clickable((By.ID, "loginbtn")))

        # Login
        driver.find_element(By.ID, "username").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "loginbtn").click()

        # Wait for dashboard or check for login failure
        wait.until(lambda d: "login/index.php" not in d.current_url or d.find_element(By.ID, "loginbtn"))

        if "login/index.php" in driver.current_url:
            print(f"❌ Login failed for {username}. Please check credentials.")
            send_telegram(f"❌ Login failed for {username}. Please delete and re-input your credentials.", chat_id)
            return

        # Search for course
        course_link = None
        wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "a")))
        courses = driver.find_elements(By.TAG_NAME, "a")
        for course in courses:
            course_text = course.text.strip().lower()
            if course_text.startswith(course_name.lower()):
                course_link = course
                break

        if course_link:
            course_link.click()
            # Wait for course page to load (look for attendance link)
            wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "a")))
        else:
            print(f"Course '{course_name}' not found on dashboard for {username}.")
            return

        # Look for 'Presensi' or 'Attendance'
        attendance_link = None
        links = driver.find_elements(By.TAG_NAME, "a")
        for link in links:
            if "presensi" in link.text.lower() or "attendance" in link.text.lower():
                attendance_link = link
                break

        if not attendance_link:
            print(f"No attendance link found for {username}.")
            send_telegram(f"No attendance link found in {course_name} for you.", chat_id)
            return

        attendance_link.click()
        # Wait for attendance page to load
        wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "a")))

        # Submit attendance
        try:
            # Click the "Submit attendance" link
            submit_link = wait.until(EC.element_to_be_clickable((By.PARTIAL_LINK_TEXT, "Submit attendance")))
            submit_link.click()

            # Wait for radio buttons to appear
            wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "label.form-check-label")))

            # Find and click the "Present" radio button
            labels = driver.find_elements(By.CSS_SELECTOR, "label.form-check-label")
            for label in labels:
                try:
                    span = label.find_element(By.CLASS_NAME, "statusdesc")
                    if span.text.strip().lower() == "present":
                        radio = label.find_element(By.TAG_NAME, "input")
                        radio.click()
                        break
                except Exception:
                    continue  # Skip labels that don't match structure

            # Click the save/submit button
            submit_button = wait.until(EC.element_to_be_clickable((By.ID, "id_submitbutton")))
            submit_button.click()

            print(f"✅ Attendance submitted for {username}!")
            send_telegram(f"✅ {course_name} attendance submitted successfully for {username}.", chat_id)

        except Exception:
            print(f"ℹ️ Could not submit attendance for {username}. It may already be marked or not available.")
            send_telegram(f"ℹ️ No  active attendance found for {course_name}. {username} (mungkin error mungkin memang gaada absen, cek gih).", chat_id)

    except Exception as e:
        print(f"❌ An error occurred for user {username}: {e}")
        send_telegram(f"❌ An error occurred during the attendance process for {course_name}.", chat_id)
    finally:
        driver.quit()


# --- Main Execution ---
if __name__ == "__main__":
    schedule = load_schedule("schedule.csv")
    current_class = get_current_class(schedule)

    if current_class:
        print(f"Current class: {current_class}")
        users = load_users()
        if not users:
            print("No users found. Please check your .env file.")
        else:
            print(f"Found {len(users)} users. Starting parallel processing...")
            # Use ThreadPoolExecutor to run login_and_attend for each user in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                # Submit a task for each user
                future_to_user = {executor.submit(login_and_attend, user, current_class): user for user in users}
                for future in concurrent.futures.as_completed(future_to_user):
                    user = future_to_user[future]
                    try:
                        future.result()  # You can handle results or exceptions here if needed
                    except Exception as exc:
                        print(f"User {user['username']} generated an exception: {exc}")
    else:
        print("No class at the moment.")
