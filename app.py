from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import requests
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Required for Flask sessions

# --- IMPORTANT ---
# This URL must be the public-facing address of your *backend* machine.
# The one you provided is perfect.
API_BASE_URL = "http://127.0.0.1:8080"
MEDIAPIPE_BASE_URL = "http://127.0.0.1:5001"

# --- Authentication & API Session ---

def login_required(f):
    """Decorator to ensure user is logged in (has API cookies)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'api_cookies' not in session:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_api_session():
    """Creates a requests.Session and loads stored cookies"""
    api_session = requests.Session()
    if 'api_cookies' in session:
        api_session.cookies.update(session['api_cookies'])
    return api_session

def get_course_data(api, course_uid):
    """Helper function to get course data"""
    try:
        resp = api.get(f"{API_BASE_URL}/course/{course_uid}")
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return None

@app.context_processor
def inject_global_data():
    """Injects data into all templates (for the sidebar)"""
    if 'api_cookies' in session:
        api = get_api_session()
        try:
            # Fetch user info for the sidebar footer
            user_resp = api.get(f"{API_BASE_URL}/@me")
            user_info = user_resp.json() if user_resp.ok else {"username": "Guest"}
            
            # Fetch chat history for the sidebar list
            chat_resp = api.get(f"{API_BASE_URL}/list_sessions")
            chat_history = chat_resp.json().get('sessions', []) if chat_resp.ok else []
            
            return dict(
                logged_in_user=user_info,
                chat_history=chat_history
            )
        except requests.exceptions.RequestException:
            # API is down or unreachable
            return dict(logged_in_user={"username": "Error"}, chat_history=[])
            
    return dict(logged_in_user=None, chat_history=[])

# --- Auth Routes (Proxy to API) ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        username = request.form.get("username")
        password = request.form.get("password")

        if not password or (not email and not username):
            flash("Please enter your email or username and password.", "error")
            return render_template("login.html")

        try:
            api_session = requests.Session()

            # Build payload dynamically — include whichever field is present
            payload = {"password": password}
            if email:
                payload["email"] = email
            else:
                payload["username"] = username

            response = api_session.post(f"{API_BASE_URL}/login", json=payload)

            if response.ok:
                # Save API's session cookies into the user's Flask session
                session["api_cookies"] = api_session.cookies.get_dict()
                return redirect(url_for("homepage"))
            else:
                flash(response.json().get("error", "Invalid credentials"), "error")

        except requests.exceptions.RequestException as e:
            flash(f"Error connecting to login service: {e}", "error")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        try:
            api_session = requests.Session()
            response = api_session.post(
                f"{API_BASE_URL}/register",
                json={"username": username, "email": email, "password": password}
            )
            if response.status_code == 201:
                # Save cookies and log in
                session['api_cookies'] = api_session.cookies.get_dict()
                return redirect(url_for('homepage'))
            else:
                flash(response.json().get("error", "Registration failed"), "error")
        except requests.exceptions.RequestException as e:
            flash(f"Error connecting to registration service: {e}", "error")
    return render_template('register.html')

@app.route("/logout")
def logout():
    """Logs the user out by proxying to API and clearing local session."""
    if 'api_cookies' in session:
        try:
            api = get_api_session()
            api.post(f"{API_BASE_URL}/logout")
        except requests.exceptions.RequestException:
            pass  # Fail silently
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))

# --- Page Routes ---

@app.route("/")
def index():
    return redirect(url_for('homepage'))

@app.route("/home")
@login_required
def homepage():
    """This page is ONLY for starting a NEW chat."""
    return render_template('homepage.html')

@app.route("/chat/<string:uid>")
@login_required
def chat_page(uid):
    """This page is for continuing an EXISTING chat."""
    api = get_api_session()
    try:
        resp = api.get(f"{API_BASE_URL}/get_session/{uid}")
        resp.raise_for_status()
        chat_data = resp.json()
    except requests.exceptions.RequestException:
        flash("Could not load chat session.", "error")
        return redirect(url_for('homepage'))
    return render_template('chat.html', chat_data=chat_data)

@app.route("/courses")
@login_required
def courses():
    api = get_api_session()
    try:
        resp = api.get(f"{API_BASE_URL}/courses")
        resp.raise_for_status()
        course_list = resp.json()
        for course in course_list:
            course['image'] = f"https://placehold.co/600x400.png?text={requests.utils.quote(course.get('course_title', 'Course'))}"
    except requests.exceptions.RequestException:
        course_list = []
        flash("Could not load courses from API.", "error")
    return render_template('courses.html', courses=course_list)

@app.route("/generate_course", methods=["POST"])
@login_required
def generate_course():
    topic = request.form.get('topic')
    if not topic:
        flash("Topic is required.", "error")
        return redirect(url_for('courses'))
    api = get_api_session()
    try:
        resp = api.post(f"{API_BASE_URL}/generate_course", json={"topic": topic})
        resp.raise_for_status()
        flash(f"Successfully generated course for '{topic}'!", "success")
    except requests.exceptions.RequestException as e:
        flash(f"Error generating course: {e}", "error")
    return redirect(url_for('courses'))

@app.route("/lessons/<string:course_uid>")
@login_required
def lessons(course_uid):
    """
    This route now just finds the first lesson and redirects
    to the 'lesson_step' route for it.
    """
    api = get_api_session()
    course_data = get_course_data(api, course_uid)
    
    if not course_data or not course_data.get('steps'):
        flash("Course not found or has no lessons.", "error")
        return redirect(url_for('courses'))
    
    # Redirect to the very first lesson
    first_step_number = course_data['steps'][0]['step_number']
    return redirect(url_for('lesson_step', course_uid=course_uid, step_number=first_step_number))

@app.route("/user_settings")
@login_required
def user_settings():
    """Fetches user data from the /@me endpoint."""
    api = get_api_session()
    try:
        resp = api.get(f"{API_BASE_URL}/@me")
        resp.raise_for_status()
        user_data = resp.json()
    except requests.exceptions.RequestException:
        user_data = {"username": "Error", "email": "Could not load data"}
    return render_template('user_settings.html', user=user_data)

# --- API PROXY ENDPOINTS (for JavaScript) ---

@app.route("/api/new_chat", methods=["POST"])
@login_required
def api_new_chat():
    data = request.get_json()
    message = data.get('message')
    if not message:
        return jsonify({"error": "No message provided"}), 400
    api = get_api_session()
    try:
        # 1. Create session
        create_resp = api.post(f"{API_BASE_URL}/create_session")
        create_resp.raise_for_status()
        uid = create_resp.json().get('uid')
        # 2. Send first message
        chat_resp = api.post(f"{API_BASE_URL}/chat", json={"uid": uid, "message": message})
        chat_resp.raise_for_status()
        # 3. Return the new chat info
        return jsonify(chat_resp.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"API Error: {e}"}), 500

@app.route("/api/chat/<string:uid>", methods=["POST"])
@login_required
def api_chat(uid):
    data = request.get_json()
    message = data.get('message')
    api = get_api_session()
    try:
        chat_resp = api.post(f"{API_BASE_URL}/chat", json={"uid": uid, "message": message})
        chat_resp.raise_for_status()
        return jsonify(chat_resp.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"API Error: {e}"}), 500

@app.route("/api/course_chat/<course_uid>/<int:step_number>", methods=["POST", "GET"])
@login_required
def api_course_chat(course_uid, step_number):
    api = get_api_session()

    if request.method == "POST":
        data = request.get_json()
        try:
            api_response = api.post(
                f"{API_BASE_URL}/course/{course_uid}/step/{step_number}/chat", 
                json=data
            )
            api_response.raise_for_status()
            return jsonify(api_response.json())
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"API Error: {e}"}), 500

    elif request.method == "GET":
        try:
            api_response = api.get(
                f"{API_BASE_URL}/course/{course_uid}/step/{step_number}/chat"
            )
            api_response.raise_for_status()
            return jsonify(api_response.json())
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"API Error: {e}"}), 500

@app.route("/lessons/<string:course_uid>/<int:step_number>")
@login_required
def lesson_step(course_uid, step_number):
    api = get_api_session()
    
    # 1. Get full course data (for the sidebar)
    course_data = get_course_data(api, course_uid)
    if not course_data:
        flash("Course not found.", "error")
        return redirect(url_for('courses'))

    # 2. Find the specific step we are on
    current_step = next((step for step in course_data.get('steps', []) if step['step_number'] == step_number), None)
    if not current_step:
        flash(f"Lesson {step_number} not found.", "error")
        return redirect(url_for('lessons', course_uid=course_uid))

    # 3. Check lesson status (started or not)
    try:
        resp = api.get(f"{API_BASE_URL}/course/{course_uid}/step/{step_number}/chat")
        
        if resp.status_code == 200:
            # 4a. History exists -> render chat template
            history_data = resp.json().get("history", [])
            return render_template(
                "lesson_chat.html",
                course_uid=course_uid,
                course_title=course_data.get('course_title', 'Course'),
                steps=course_data.get('steps', []),
                step=current_step,
                active_step_number=step_number,
                history=history_data
            )
        else:
            # 4b. Not found (or other error) -> render 'not started' template
            return render_template(
                "lesson_not_started.html",
                course_uid=course_uid,
                course_title=course_data.get('course_title', 'Course'),
                steps=course_data.get('steps', []),
                step=current_step,
                active_step_number=step_number
            )

    except requests.exceptions.RequestException as e:
        flash(f"Error loading lesson: {e}", "error")
        return redirect(url_for('courses'))

@app.route("/lessons/<course_uid>/<int:step_number>/start", methods=["POST"])
@login_required
def start_lesson(course_uid, step_number):
    api = get_api_session()
    try:
        api_response = api.post(
            f"{API_BASE_URL}/course/{course_uid}/step/{step_number}/chat",
            json={"start": True}
        )
        api_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        flash(f"Error starting lesson: {e}", "error")
        
    # After POSTing, redirect back to the GET route for the same step
    return redirect(url_for('lesson_step', course_uid=course_uid, step_number=step_number))

@app.route("/lessons/<string:course_uid>/calibrate")
@login_required
def calibrate_camera(course_uid):
    """
    Renders the camera calibration page for a specific course.
    """
    api = get_api_session()
    
    # We still need course data to populate the sidebar
    course_data = get_course_data(api, course_uid)
    if not course_data:
        flash("Course not found.", "error")
        return redirect(url_for('courses'))

    return render_template(
        "calibrate.html",
        course_uid=course_uid,
        course_title=course_data.get('course_title', 'Course'),
        steps=course_data.get('steps', []),
        # Pass 'calibrate' to make the sidebar link active
        active_step_number='calibrate' 
    )

@app.route("/lessons/<string:course_uid>/exam")
@login_required
def take_exam(course_uid):
    api = get_api_session()
    course_data = get_course_data(api, course_uid)
    if not course_data:
        flash("Course not found.", "error")
        return redirect(url_for('courses'))

    # Fetch exam from Engine
    try:
        resp = api.get(f"{API_BASE_URL}/course/{course_uid}/exam")
        if resp.status_code == 200:
            exam_data = resp.json().get('exam')
            exam_uid = resp.json().get('exam_uid')
        else:
            flash("Exam not available yet.", "info")
            return redirect(url_for('courses'))
    except requests.exceptions.RequestException:
        flash("Could not load exam data.", "error")
        return redirect(url_for('courses'))

    return render_template(
        "exam.html",
        course_uid=course_uid,
        exam_uid=exam_uid,
        exam=exam_data,
        course_title=course_data.get('course_title', 'Course'),
        steps=course_data.get('steps', []),
        active_step_number='exam',
        socket_url=MEDIAPIPE_BASE_URL
    )

    return render_template(
        "exam.html",
        course_uid=course_uid,
        exam_uid=exam_uid,
        exam=exam_data,
        course_title=course_data.get('course_title', 'Course'),
        steps=course_data.get('steps', []),
        active_step_number='exam'
    )

@app.route("/api/course/<string:course_uid>/exam/submit", methods=["POST"])
@login_required
def submit_exam_proxy(course_uid):
    api = get_api_session()
    data = request.json
    
    # Forward to Engine
    try:
        resp = api.post(f"{API_BASE_URL}/course/{course_uid}/exam/submit", json=data)
        if resp.status_code == 200:
            return jsonify(resp.json())
        else:
            return jsonify({"error": "Submission failed at engine"}), resp.status_code
    except requests.exceptions.RequestException as e:
         return jsonify({"error": str(e)}), 500

@app.route("/lessons/<string:course_uid>/score")
@login_required
def exam_score(course_uid):
    api = get_api_session()
    course_data = get_course_data(api, course_uid)
    if not course_data:
        flash("Course not found.", "error")
        return redirect(url_for('courses'))
    
    # Get username from API
    try:
        user_resp = api.get(f"{API_BASE_URL}/@me")
        if user_resp.ok:
            username = user_resp.json().get('username', 'Student')
        else:
            username = 'Student'
    except requests.exceptions.RequestException:
        username = 'Student'
    
    score = request.args.get('score', 0, type=int)
    total = request.args.get('total', 0, type=int)
    correct_count = request.args.get('correct_count', 0, type=int)

    return render_template(
        "score.html",
        course_uid=course_uid,
        course_title=course_data.get('course_title', 'Course'),
        steps=course_data.get('steps', []),
        active_step_number='exam',
        score=score,
        total=total,
        correct_count=correct_count,
        username=username
    )

@app.route("/google_auth", methods=["POST"])
def google_auth():
    data = request.get_json()
    credential = data.get("credential")

    if not credential:
        return jsonify({"success": False, "error": "Missing credential"}), 400

    api_session = requests.Session()

    try:
        # Forward credential to main backend ("real API" on :8080)
        resp = api_session.post(
            f"{API_BASE_URL}/auth/google",
            json={"credential": credential}
        )

        if resp.ok:
            # Save backend cookies into the frontend session
            data = resp.json()
            session["access_token"] = data["token"]
            session["user"] = data["user"]
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": resp.json()}), 401

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Run on 0.0.0.0 to be accessible on your network
    app.run(debug=True, port=5000, host='0.0.0.0')