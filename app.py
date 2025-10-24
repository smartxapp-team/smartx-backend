from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta
from pytz import timezone
import logging
from concurrent.futures import ThreadPoolExecutor
from flask_jwt_extended import create_access_token, JWTManager

# Configure logging to suppress verbose "GET /..." output
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# --- Setup Flask App ---
app = Flask(__name__)
# IMPORTANT: In a production environment, this secret key should be
# stored securely (e.g., as an environment variable) and not hardcoded.
app.config["JWT_SECRET_KEY"] = "a_super_secret_key_for_smartx_final"
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=30)
jwt = JWTManager(app)

# --- In-Memory Storage & Constants ---
SESSIONS_CACHE = {}
INDIA_TIMEZONE = timezone('Asia/Kolkata')
CACHE_DURATION_MINUTES = 15

# =======================================================
# 1. CORE UTILITY AND SESSION FUNCTIONS
# =======================================================

def get_data_from_cache(user_id, cache_type):
    session_data = SESSIONS_CACHE.get(user_id, {})
    cache_ts = session_data.get(f'{cache_type}_cache_timestamp')
    cache_data = session_data.get(f'{cache_type}_cache_data')
    now = datetime.now(INDIA_TIMEZONE)
    if cache_ts and cache_data and now < cache_ts + timedelta(minutes=CACHE_DURATION_MINUTES):
        print(f"[SERVER LOG] Returning fresh CACHED data for {user_id} - type: {cache_type}")
        return cache_data
    print(f"[SERVER LOG] Cache stale for {user_id} - type: {cache_type}. Fetching new data.")
    return None

def set_data_in_cache(user_id, cache_type, data):
    if user_id in SESSIONS_CACHE:
        SESSIONS_CACHE[user_id][f'{cache_type}_cache_timestamp'] = datetime.now(INDIA_TIMEZONE)
        SESSIONS_CACHE[user_id][f'{cache_type}_cache_data'] = data
        print(f"[SERVER LOG] Stored new data in cache for {user_id} - type: {cache_type}")

def perform_login(username, password):
    print(f"\n[SERVER LOG] Attempting to log in user: {username}...")
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://samvidha.iare.ac.in/index'}
    data = {'username': username, 'password': password}
    with requests.Session() as s:
        try:
            s.get("https://samvidha.iare.ac.in/index", timeout=10)
            s.post("https://samvidha.iare.ac.in/pages/login/checkUser.php", headers=headers, data=data, timeout=10)
            response = s.get("https://samvidha.iare.ac.in/home", timeout=10)
            if '<title>IARE - Dashboard - Student</title>' in response.text:
                print(f"[SERVER LOG] Login successful for {username}.")
                return {'cookies': s.cookies.get_dict()}
            print(f"[SERVER LOG] Login FAILED for {username}. Credentials might be invalid.")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[SERVER LOG] NETWORK ERROR during login: {e}")
            return None

def fetch_secure_page(session_cookies, url):
    try:
        with requests.Session() as s:
            s.cookies.update(session_cookies)
            response = s.get(url, timeout=15)
            if '<title>IARE - Login</title>' in response.text or '/index' in response.url:
                return "SESSION_EXPIRED", None
            return "SUCCESS", response
    except requests.exceptions.RequestException:
        return "NETWORK_ERROR", None
    except Exception as e:
        return f"GENERIC_ERROR: {e}", None

# =======================================================
# 2. SCRAPING LOGIC & HELPERS
# =======================================================



def get_branch_acronym(branch_name):
    branch_map = {
        "COMPUTER SCIENCE AND ENGINEERING": "CSE",
        "ELECTRONICS AND COMMUNICATION ENGINEERING": "ECE",
        "INFORMATION TECHNOLOGY": "IT",
        "MECHANICAL ENGINEERING": "MECH",
        "CIVIL ENGINEERING": "CIVIL",
        "AERONAUTICAL ENGINEERING": "AERO",
        "COMPUTER SCIENCE AND INFORMATION TECHNOLOGY": "CSIT",
        "COMPUTER SCIENCE AND ENGINEERING (ARTIFICIAL INTELLIGENCE AND MACHINE LEARNING)": "CSE (AI & ML)",
        "COMPUTER SCIENCE AND ENGINEERING (DATA SCIENCE)": "CSE (DS)",
        "COMPUTER SCIENCE AND ENGINEERING (CYBER SECURITY)": "CSE (CS)",
    }
    branch_upper = branch_name.upper()
    for full_name, acronym in branch_map.items():
        if full_name in branch_upper:
            return acronym
    words = branch_name.replace('(', '').replace(')', '').split()
    return "".join(word[0] for word in words if word[0].isupper())

def scrape_profile_details(username, session_cookies):
    cached_data = get_data_from_cache(username, 'profile')
    if cached_data: return cached_data

    status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=profile')
    if status != "SUCCESS": return {"error": status}

    try:
        soup = BeautifulSoup(response.text, 'lxml')

        def find_detail(label):
            dt = soup.find('dt', class_='col-sm-4', string=label)
            return dt.find_next_sibling('dd', class_='col-sm-8').get_text(strip=True) if dt else 'N/A'

        roll_no = find_detail('Roll Number')
        if roll_no == 'N/A':
            return {"error": "Could not find Roll Number on profile page."}

        gender_raw = find_detail('Gender')
        gender = 'Male' if gender_raw == 'M' else 'Female' if gender_raw == 'F' else 'N/A'

        doj_raw = find_detail('Date of Joining')
        try:
            joining_year = int(doj_raw.split('-')[-1])
            batch = f"{joining_year}-{joining_year + 4}"
        except (ValueError, IndexError):
            batch = 'N/A'
        
        year_sem_raw = find_detail('Year/Sem')
        def format_year_sem(raw_str):
            if raw_str == 'N/A' or 'B.Tech' not in raw_str:
                return 'N/A'
            parts = raw_str.replace('B.Tech', '').strip().split()
            return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else raw_str

        profile_details = {
            'full_name': find_detail('Name').upper(),
            'roll_no': roll_no,
            'branch': get_branch_acronym(find_detail('Branch').split('(')[0].strip()),
            'year_sem': format_year_sem(year_sem_raw),
            'section': find_detail('Section'),
            'gender': gender,
            'email': f"{roll_no.lower()}@iare.ac.in",
            'batch': batch,
            'profile_pic_url': f"https://iare-data.s3.ap-south-1.amazonaws.com/uploads/STUDENTS/{roll_no}/{roll_no}.jpg"
        }

        set_data_in_cache(username, 'profile', profile_details)
        return profile_details
    except Exception as e:
        return {"error": f"Failed to parse profile HTML: {e}"}

def get_attendance_color(percentage):
    if percentage >= 75:
        return 'green'
    elif percentage >= 65:
        return 'orange'
    else:
        return 'red'

def fetch_attendance(username, session_cookies):
    cached_data = get_data_from_cache(username, 'att')
    if cached_data:
        return cached_data

    status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=stud_att_STD')
    if status != "SUCCESS":
        return {"error": status}

    try:
        soup = BeautifulSoup(response.text, 'lxml')

        # --- ✅ More robustly get "Last Date of Semester" ---
        last_sem_date = 'N/A'
        try:
            # Find the tag (th or td) containing the label text
            label_tag = soup.find(lambda tag: tag.name in ['th', 'td'] and 'Last Date of Semester' in tag.get_text(strip=True))
            if label_tag:
                # Find the next 'td' sibling, which should contain the date value
                value_tag = label_tag.find_next_sibling('td')
                if value_tag:
                    last_sem_date = value_tag.get_text(strip=True)
        except Exception:
            pass # Fail silently to avoid crashing the whole attendance fetch

        # --- 📊 Attendance Table ---
        tables = soup.find_all('table', class_='table-head-fixed')
        if len(tables) < 2:
            return {"error": "Attendance table not found"}

        table = tables[1]
        rows = table.tbody.find_all('tr')

        courses = []
        total_conducted = 0
        total_attended = 0

        for row in rows:
            cells = [cell.get_text(strip=True) for cell in row.find_all('td')]
            if len(cells) > 8:
                try:
                    percentage = float(cells[7])
                    course_data = {
                        "name": cells[2],
                        "conducted": int(cells[5]),
                        "attended": int(cells[6]),
                        "percentage": percentage,
                        "status": cells[8],
                        "color_code": get_attendance_color(percentage)
                    }
                    courses.append(course_data)
                    total_conducted += course_data["conducted"]
                    total_attended += course_data["attended"]
                except (ValueError, IndexError):
                    continue

        overall_percentage = (total_attended / total_conducted * 100) if total_conducted > 0 else 0

        attendance_data = {
            "courses": courses,
            "overall_percentage": round(overall_percentage, 2),
            "last_sem_date": last_sem_date or "N/A"
        }

        set_data_in_cache(username, 'att', attendance_data)
        return attendance_data

    except Exception as e:
        return {"error": f"Failed to parse attendance HTML: {e}"}


def fetch_timetable(username, session_cookies):
    cached_data = get_data_from_cache(username, 'tt')
    if cached_data:
        return cached_data

    status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=TT_std')
    if status != "SUCCESS":
        return {"error": status}

    try:
        soup = BeautifulSoup(response.text, 'lxml')
        ay_select = soup.find('select', {'name': 'ay'})
        ay = ay_select.find('option').get('value') if ay_select and ay_select.find('option') else None
        sec_data_select = soup.find('select', {'name': 'sec_data'})
        sec_data = sec_data_select.find_all('option')[1].get('value') if sec_data_select and len(sec_data_select.find_all('option')) > 1 else None

        if not all([ay, sec_data]):
            return {"error": "Could not determine AY or Section for timetable."}

        payload = {'ay': ay, 'sec_data': sec_data, 'btn_faculty_tt': 'show'}

        with requests.Session() as s:
            s.cookies.update(session_cookies)
            r = s.post('https://samvidha.iare.ac.in/home?action=TT_std', data=payload, timeout=15)
            soup = BeautifulSoup(r.text, 'lxml')

        tables = soup.find_all('table', class_='table-bordered')
        if len(tables) < 2:
            return {"error": "Timetable structure not found."}

        subject_map = {
            cells[3]: cells[2]
            for row in tables[1].find_all('tr')[1:]
            if len(cells := [cell.get_text(strip=True) for cell in row.find_all('td')]) >= 4
        }

        def get_shortcut(name: str):
            ignore_words = {"of", "and", "the"}
            name = name.strip()
            if "/" in name:
                return name.split('/')[0].strip()
            if len(name) <= 7:
                return name.upper()
            words = name.split()
            return ''.join(word[0].upper() for word in words if word.lower() not in ignore_words)

        timetable = {}

        for row in tables[0].find_all('tr')[2:]:
            day_cell = row.find('th')
            if not day_cell:
                continue

            day_name = day_cell.get_text(strip=True, separator='<br>').split('<br>')[0]
            periods = []

            last_subject_info = None

            for i, cell in enumerate(row.find_all('td'), 1):
                text = cell.get_text(strip=True, separator='<br>')
                parts = [p.strip() for p in text.split('<br>') if p.strip()]

                if not parts:
                    last_subject_info = None
                    continue

                short_code = parts[0].split(' ')[0]
                full_subject_name = subject_map.get(short_code)

                room = ""
                for p in parts:
                    if "Room" in p:
                        room = p.replace('Room : ', '').strip()

                if full_subject_name:
                    short_name = get_shortcut(full_subject_name)

                    current_subject = {
                        'period': f"Period - {i}",
                        'subject_full': full_subject_name,
                        'subject_short': short_name,
                        'room': room
                    }
                    periods.append(current_subject)

                    if "Laboratory" in full_subject_name:
                        last_subject_info = current_subject
                    else:
                        last_subject_info = None

                else:
                    if last_subject_info:
                        continued_subject = last_subject_info.copy()
                        continued_subject['period'] = f"Period - {i}"
                        if room:
                            continued_subject['room'] = room
                        periods.append(continued_subject)
                    else:
                        periods.append({
                            'period': f"Period - {i}",
                            'subject_full': short_code,
                            'subject_short': get_shortcut(short_code),
                            'room': room
                        })
                        last_subject_info = None

            if day_name and periods:
                timetable[day_name] = periods

        today_name = datetime.now(INDIA_TIMEZONE).strftime('%A')
        today_schedule = timetable.get(today_name, [])

        full_timetable_data = {"timetable": timetable, "today_schedule": today_schedule}
        set_data_in_cache(username, 'tt', full_timetable_data)
        return full_timetable_data

    except Exception as e:
        return {"error": f"Failed to parse timetable HTML: {e}"}

def fetch_bio_log_data(username, session_cookies):
    cached_data = get_data_from_cache(username, 'bio_log')
    if cached_data:
        return cached_data

    status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=std_bio')
    if status != "SUCCESS":
        return {"error": status}

    try:
        soup = BeautifulSoup(response.text, 'lxml')
        table = soup.find('table', class_='table-striped')
        if not table or not table.tbody:
            return {"error": "Could not find biometric data table."}

        # Extract all rows
        rows = [ [cell.get_text(strip=True) for cell in row.find_all('td')] for row in table.tbody.find_all('tr') ]
        if not rows:
            return {"error": "No data rows found."}

        # 🔍 Find which column contains 'Present' or 'Absent'
        status_col_index = None
        for row in rows:
            for i, text in enumerate(row):
                if "present" in text.lower() or "absent" in text.lower():
                    status_col_index = i
                    break
            if status_col_index is not None:
                break

        if status_col_index is None:
            return {"error": "Could not find any 'Present' or 'Absent' column."}

        # 🧾 Build structured list: s_no, date, status
        bio_log = []
        for row in rows:
            s_no = row[0] if len(row) > 0 else ""
            date = row[3] if len(row) > 3 else ""
            status_text = row[status_col_index] if len(row) > status_col_index else ""
            if "present" in status_text.lower() or "absent" in status_text.lower():
                bio_log.append({
                    "s_no": s_no,
                    "date": date,
                    "status": status_text
                })

        bio_data = {"bio_log": bio_log}
        set_data_in_cache(username, 'bio_log', bio_data)
        return bio_data

    except Exception as e:
        return {"error": f"Failed to parse biometric log HTML: {e}"}


def fetch_bio_summary(username, session_cookies):
    bio_log_data = fetch_bio_log_data(username, session_cookies)
    if 'error' in bio_log_data:
        return bio_log_data

    present_days = sum(1 for log in bio_log_data['bio_log'] if log['status'] == 'P')
    total_days = len(bio_log_data['bio_log'])
    percentage = (present_days / total_days * 100) if total_days > 0 else 0

    return {
        'present_days': present_days,
        'total_days': total_days,
        'percentage': round(percentage, 2)
    }

def fetch_lab_deadlines_data(session_cookies, username):
    cached_data = get_data_from_cache(username, 'lab')
    if cached_data: return cached_data
    main_url = 'https://samvidha.iare.ac.in/home?action=labrecord_std'
    details_url = 'https://samvidha.iare.ac.in/pages/student/lab_records/ajax/day2day.php'
    grouped_data = {}
    try:
        with requests.Session() as s:
            s.cookies.update(session_cookies)
            main_page_response = s.get(main_url, timeout=15)
            if '/index' in main_page_response.url: return {"error": "Session Expired"}
            main_soup = BeautifulSoup(main_page_response.text, 'lxml')
            ay = main_soup.find('input', {'name': 'ay'}).get('value')
            rollno = main_soup.find('input', {'name': 'rollno'}).get('value')
            subject_options = main_soup.select('select[name="ddlsub_code"] option')
            subjects = [{'code': opt.get('value'), 'name': opt.text} for opt in subject_options if opt.get('value')]
            for subject in subjects:
                code = subject['code']
                full_name = subject['name']
                display_name = full_name.split(' - ')[-1].strip() if ' - ' in full_name else full_name
                grouped_data[code] = {'subject_name': display_name, 'deadlines': []}
                submitted_payload = {'rollno': rollno, 'ay': ay, 'sub_code': code, 'action': 'day2day_lab'}
                submitted_response = s.post(details_url, data=submitted_payload, timeout=10)
                submitted_json = submitted_response.json()
                submitted_weeks = {item['week_no'] for item in submitted_json.get('data', [])}
                all_labs_payload = {'ay': ay, 'sub_code': code, 'action': 'get_exp_list'}
                details_response = s.post(details_url, data=all_labs_payload, timeout=10)
                details_soup = BeautifulSoup(details_response.text, 'lxml')
                table = details_soup.find('table')
                if not table: continue
                for row in table.find_all('tr')[1:]:
                    cells = [cell.get_text(strip=True) for cell in row.find_all('td')]
                    if len(cells) >= 5:
                        week_text = cells[0].replace('Week-', '').strip()
                        is_submitted = week_text in submitted_weeks
                        grouped_data[code]['deadlines'].append({"week": cells[0], "title": cells[2], "due_date_str": cells[4], "submitted": is_submitted})
            set_data_in_cache(username, 'lab', grouped_data)
            return grouped_data
    except Exception as e: return {"error": f"Failed to fetch lab data: {e}"}

def fetch_results(username, session_cookies):
    cached_data = get_data_from_cache(username, 'results')
    if cached_data: return cached_data

    status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=g_stud_results')
    if status != "SUCCESS": return {"error": status}

    try:
        soup = BeautifulSoup(response.text, 'lxml')
        results = []
        cgpa = 'N/A'

        table = soup.find('table', class_='table-bordered')
        if table:
            for row in table.find_all('tr')[1:]:
                cells = [cell.get_text(strip=True) for cell in row.find_all('td')]
                if len(cells) >= 10:
                    results.append({
                        'semester': cells[1],
                        'sgpa': cells[9]
                    })

        cgpa_element = soup.find('h3', class_='text-center')
        if cgpa_element and 'CGPA' in cgpa_element.text:
            cgpa = cgpa_element.text.split(':')[-1].strip()

        results_data = {'semesters': results, 'cgpa': cgpa}
        set_data_in_cache(username, 'results', results_data)
        return results_data
    except Exception as e:
        return {"error": f"Failed to parse results HTML: {e}"}

def fetch_attendance_register(username, session_cookies):
     cached_data = get_data_from_cache(username, 'attendance_register')
     if cached_data:
         return cached_data
     status, response = fetch_secure_page(session_cookies, 'https://samvidha.iare.ac.in/home?action=course_content')
     if status != "SUCCESS":
         return {"error": status}
     try:
         soup = BeautifulSoup(response.text, 'lxml')
         table = soup.find('table', class_='table-sm')
         if not table or not table.tbody:
             return {"error": "Could not find attendance register table."}
         all_subjects = set()
         all_dates = set()
         attendance_data = {}
         current_subject = None
         rows = table.tbody.find_all('tr')
         for row in rows:
             header_cell = row.find('th', class_='bg-pink')
             if header_cell:
                 full_subject_name = header_cell.get_text(strip=True).split('-', 1)[-1].strip()
                 current_subject = full_subject_name
                 if current_subject not in attendance_data:
                     attendance_data[current_subject] = {}
                     all_subjects.add(current_subject)
                 continue
             cells = row.find_all('td')
             if len(cells) >= 5 and current_subject:
                 date_str = cells[1].get_text(strip=True)
                 status = cells[4].get_text(strip=True)
                 if date_str and status in ('PRESENT', 'ABSENT'):
                     try:
                         date_obj = datetime.strptime(date_str, '%d %b, %Y')
                         formatted_date = date_obj.strftime('%Y-%m-%d')
                         all_dates.add(formatted_date)
                         attendance_data[current_subject][formatted_date] = status
                     except ValueError:
                         continue
         
         sorted_subjects = sorted(list(all_subjects))
         sorted_dates = sorted(list(all_dates), reverse=True)
         final_register = {}
         for subject in sorted_subjects:
             final_register[subject] = []
             for date in sorted_dates:
                 status = attendance_data.get(subject, {}).get(date, 'N/A')
                 final_register[subject].append(status)
         result = {
             "subjects": sorted_subjects,
             "dates": sorted_dates,
             "register": final_register
         }
         
         set_data_in_cache(username, 'attendance_register', result)
         return result
     except Exception as e:
         import traceback
         print(traceback.format_exc())
         return {"error": f"Failed to parse attendance register: {e}"}

# =======================================================
# 3. API ENDPOINTS FOR FLUTTER APP
# =======================================================

@app.route('/')
def home():
    return jsonify({"message": "SmartX Backend is running successfully!"})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({"error": "Request must be JSON with username and password"}), 400
    username = data['username']
    password = data['password']
    session_data = perform_login(username, password)
    if session_data:
        SESSIONS_CACHE[username] = session_data
        access_token = create_access_token(identity=username)
        return jsonify({"message": "Login successful", "username": username, "token": access_token})
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route('/api/academic_info/<username>')
def api_academic_info(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data:
        return jsonify({"error": "User not logged in or session expired"}), 401

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_attendance = executor.submit(fetch_attendance, username, session_data['cookies'])
        future_bio = executor.submit(fetch_bio_summary, username, session_data['cookies'])
        future_results = executor.submit(fetch_results, username, session_data['cookies'])

        attendance_data = future_attendance.result()
        bio_data = future_bio.result()
        results_data = future_results.result()

    class_attendance = attendance_data.get('overall_percentage', 0)
    bio_attendance = bio_data.get('percentage', 0)

    cgpa = 0.0
    latest_sgpa = 0.0
    if 'error' not in results_data and results_data.get('semesters'):
        try:
            cgpa = float(results_data.get('cgpa', 0.0))
        except (ValueError, TypeError):
            cgpa = 0.0

        for sem in reversed(results_data['semesters']):
            try:
                sgpa_val = float(sem.get('sgpa', 0.0))
                if sgpa_val > 0:
                    latest_sgpa = sgpa_val
                    break
            except (ValueError, TypeError):
                continue

    return jsonify({
        "class_attendance": class_attendance,
        "bio_attendance": bio_attendance,
        "sgpa": latest_sgpa,
        "cgpa": cgpa
    })

@app.route('/api/dashboard/<username>')
def api_dashboard(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in or session expired"}), 401

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_timetable = executor.submit(fetch_timetable, username, session_data['cookies'])
        future_bio_summary = executor.submit(fetch_bio_summary, username, session_data['cookies'])
        future_labs = executor.submit(fetch_lab_deadlines_data, session_data['cookies'], username)

        timetable_data = future_timetable.result()
        bio_summary_data = future_bio_summary.result()
        lab_data = future_labs.result()

    unsubmitted_labs = []
    if 'error' not in lab_data:
        all_deadlines = []
        for code, data in lab_data.items():
            for deadline in data['deadlines']:
                if not deadline['submitted']:
                    try:
                        due_date = datetime.strptime(deadline['due_date_str'], '%d-%m-%Y').date()
                        deadline['due_date_obj'] = due_date.isoformat()
                        deadline['course_name'] = data['subject_name']
                        all_deadlines.append(deadline)
                    except (ValueError, KeyError): continue
        upcoming_deadlines = sorted([d for d in all_deadlines if datetime.fromisoformat(d['due_date_obj']).date() >= datetime.now().date()], key=lambda x: x['due_date_obj'])
        unsubmitted_labs = upcoming_deadlines

    return jsonify({
        "timetable_data": timetable_data,
        "bio_summary_data": bio_summary_data,
        "deadline_summary_data": {"unsubmitted_labs": unsubmitted_labs}
    })

@app.route('/api/profile/<username>')
def api_profile(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    return jsonify(scrape_profile_details(username, session_data['cookies']))

@app.route('/api/attendance/<username>')
def api_attendance(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    return jsonify(fetch_attendance(username, session_data['cookies']))

@app.route('/api/timetable/<username>')
def api_timetable(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    return jsonify(fetch_timetable(username, session_data['cookies']))

@app.route('/api/bio/<username>')
def api_bio(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    return jsonify(fetch_bio_log_data(username, session_data['cookies']))

@app.route('/api/results/<username>')
def api_results(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    return jsonify(fetch_results(username, session_data['cookies']))

@app.route('/api/labs/courses/<username>')
def api_lab_courses(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    lab_data = fetch_lab_deadlines_data(session_data['cookies'], username)
    if 'error' in lab_data: return jsonify(lab_data), 500
    summary = [{'code': code, 'name': data['subject_name']} for code, data in lab_data.items()]
    return jsonify({"courses": summary})

@app.route('/api/labs/details/<username>/<course_code>')
def api_lab_details(username, course_code):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data: return jsonify({"error": "User not logged in"}), 401
    lab_data = fetch_lab_deadlines_data(session_data['cookies'], username)
    if 'error' in lab_data: return jsonify(lab_data), 500
    course_details = lab_data.get(course_code)
    if not course_details: return jsonify({"error": "Invalid course code"}), 404
    return jsonify(course_details)

@app.route('/api/attendance_register/<username>')
def api_attendance_register(username):
    session_data = SESSIONS_CACHE.get(username)
    if not session_data:
        return jsonify({"error": "User not logged in or session expired"}), 401
    return jsonify(fetch_attendance_register(username, session_data['cookies']))


# --- MAIN RUN BLOCK ---
if __name__ == '__main__':
    HOST_IP = '0.0.0.0'
    PORT = 5000
    print(f"Starting Flask server on http://{HOST_IP}:{PORT}")
    app.run(host=HOST_IP, port=PORT, debug=True, threaded=True)