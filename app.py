from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import requests
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Required for Flask sessions

# --- IMPORTANT ---
# This URL must be the public-facing address of your *backend* machine.
# The one you provided is perfect.
API_BASE_URL = "https://llm-api.bimazznxt.my.id"

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
    """This route renders a standalone page, no base.html."""
    api = get_api_session()
    try:
        resp = api.get(f"{API_BASE_URL}/course/{course_uid}")
        resp.raise_for_status()
        course_data = resp.json()
    except requests.exceptions.RequestException:
        flash("Error finding course.", "error")
        return redirect(url_for('courses'))
    return render_template(
        'lessons.html', 
        course_title=course_data.get('course_title', 'Course'),
        steps=course_data.get('steps', []),
        course_uid=course_uid
    )

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

if __name__ == "__main__":
    # Run on 0.0.0.0 to be accessible on your network
    app.run(debug=True, port=5000, host='0.0.0.0')