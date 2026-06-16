from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify, Response
import os, zipfile, subprocess, shutil, json, sys, datetime, threading, time
from functools import wraps

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY_CHANGE_ME"

# ---------- CONFIG ----------
PASSWORD = "SEMYM4X"               # Change this
UPLOAD_FOLDER = "uploads"
MAX_RUNNING = 3
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------- GLOBAL STATE ----------
processes = {}
process_output = {}
process_locks = {}
USER = "admin"

STARTUP_CONFIG_FILE = "startup_configs.json"

def load_startup_configs():
    if os.path.exists(STARTUP_CONFIG_FILE):
        with open(STARTUP_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_startup_configs(configs):
    with open(STARTUP_CONFIG_FILE, "w") as f:
        json.dump(configs, f, indent=2)

def get_startup_file(app_name):
    configs = load_startup_configs()
    return configs.get(app_name, "main.py")

def set_startup_file(app_name, filename):
    configs = load_startup_configs()
    configs[app_name] = filename
    save_startup_configs(configs)

# ---------- BOT CORE (unchanged) ----------
def start_app(app_name):
    user_dir = os.path.join(UPLOAD_FOLDER, USER)
    app_dir = os.path.join(user_dir, app_name)
    zip_path = os.path.join(app_dir, "app.zip")
    extract_dir = os.path.join(app_dir, "extracted")
    log_path = os.path.join(app_dir, "logs.txt")

    if not os.path.exists(zip_path):
        return False, "ZIP file not found"

    key = (USER, app_name)
    if key in processes and processes[key].poll() is None:
        return False, "Already running"

    if not os.path.exists(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(extract_dir)
        except Exception as e:
            return False, f"ZIP extraction failed: {str(e)}"

    req_file = os.path.join(extract_dir, "requirements.txt")
    if os.path.exists(req_file) and not os.path.exists(os.path.join(extract_dir, "requirements_installed.txt")):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"],
                           check=True, capture_output=True, timeout=120)
            with open(os.path.join(extract_dir, "requirements_installed.txt"), "w") as f:
                f.write("installed")
        except Exception as e:
            print(f"pip warning: {e}")

    startup_file = get_startup_file(app_name)
    found_main = None
    target_dir = extract_dir

    for root, dirs, files in os.walk(extract_dir):
        if startup_file in files:
            found_main = os.path.join(root, startup_file)
            target_dir = root
            break
    if not found_main:
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f in ["main.py", "app.py", "bot.py", "index.py", "run.py", "start.py"]:
                    found_main = os.path.join(root, f)
                    target_dir = root
                    break
            if found_main:
                break

    if not found_main:
        return False, f"No Python file found to run"

    try:
        log = open(log_path, "a")
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        p = subprocess.Popen(
            [sys.executable, "-u", os.path.basename(found_main)],
            cwd=target_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env
        )
        processes[key] = p
        process_locks[key] = threading.Lock()
        process_output[key] = []

        def read_output():
            try:
                while True:
                    line = p.stdout.readline()
                    if not line:
                        break
                    with process_locks[key]:
                        process_output[key].append(line)
                        if len(process_output[key]) > 2000:
                            process_output[key] = process_output[key][-1000:]
                    try:
                        log.write(line)
                        log.flush()
                    except:
                        pass
            except:
                pass
            finally:
                try:
                    log.close()
                except:
                    pass

        threading.Thread(target=read_output, daemon=True).start()
        time.sleep(0.5)
        if p.poll() is not None and p.returncode != 0:
            return False, f"Process exited with code {p.returncode}"
        return True, f"Started {os.path.basename(found_main)}"
    except Exception as e:
        return False, str(e)

def stop_app(app_name):
    key = (USER, app_name)
    p = processes.get(key)
    if p:
        try:
            p.terminate()
            try:
                p.wait(timeout=3)
            except:
                p.kill()
                p.wait()
        except:
            pass
        finally:
            processes.pop(key, None)
            process_locks.pop(key, None)
            return True
    return False

def restart_app(app_name):
    stop_app(app_name)
    time.sleep(0.5)
    return start_app(app_name)

def get_directory_structure(app_name, path=""):
    app_dir = os.path.join(UPLOAD_FOLDER, USER, app_name, "extracted")
    full_path = os.path.join(app_dir, path)
    if not os.path.exists(full_path):
        return []
    items = []
    try:
        for item in sorted(os.listdir(full_path), key=lambda x: (not os.path.isdir(os.path.join(full_path, x)), x.lower())):
            item_path = os.path.join(path, item) if path else item
            full_item_path = os.path.join(full_path, item)
            is_dir = os.path.isdir(full_item_path)
            items.append({
                "name": item,
                "path": item_path,
                "is_dir": is_dir,
                "size": os.path.getsize(full_item_path) if not is_dir else 0,
                "modified": datetime.datetime.fromtimestamp(os.path.getmtime(full_item_path)).strftime("%Y-%m-%d %H:%M")
            })
    except Exception as e:
        print(f"Directory error: {e}")
    return items

# ---------- AUTH ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------- ROUTES (unchanged) ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Wrong password")
    return render_template_string(LOGIN_TEMPLATE, error=None)

@app.route("/dashboard")
@login_required
def dashboard():
    user_dir = os.path.join(UPLOAD_FOLDER, USER)
    os.makedirs(user_dir, exist_ok=True)

    apps = []
    if os.path.exists(user_dir):
        for name in os.listdir(user_dir):
            app_path = os.path.join(user_dir, name)
            if os.path.isdir(app_path):
                key = (USER, name)
                running = key in processes and processes[key].poll() is None
                log_file = os.path.join(app_path, "logs.txt")
                log_data = ""
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding='utf-8', errors='ignore') as f:
                            log_data = f.read()[-2000:]
                    except:
                        pass
                if running and key in process_output:
                    with process_locks.get(key, threading.Lock()):
                        live = ''.join(process_output[key][-100:])
                        if live:
                            log_data = live
                apps.append({
                    "name": name,
                    "running": running,
                    "log": log_data,
                    "startup_file": get_startup_file(name)
                })
    return render_template_string(DASHBOARD_TEMPLATE, apps=apps)

@app.route("/upload", methods=["POST"])
@login_required
def upload_app():
    file = request.files.get("file")
    if file and file.filename.endswith(".zip"):
        app_name = file.filename.replace(".zip", "").replace(" ", "_")
        user_dir = os.path.join(UPLOAD_FOLDER, USER)
        app_dir = os.path.join(user_dir, app_name)

        stop_app(app_name)
        shutil.rmtree(app_dir, ignore_errors=True)
        os.makedirs(app_dir, exist_ok=True)
        file.save(os.path.join(app_dir, "app.zip"))

        extract_dir = os.path.join(app_dir, "extracted")
        try:
            with zipfile.ZipFile(os.path.join(app_dir, "app.zip"), 'r') as z:
                z.extractall(extract_dir)
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    if f in ["main.py", "app.py", "bot.py", "index.py", "run.py", "start.py"]:
                        set_startup_file(app_name, f)
                        break
        except Exception as e:
            flash(f"Upload warning: {str(e)}", "warning")
        flash("✅ Bot uploaded successfully!", "success")
    else:
        flash("Please upload a .zip file", "error")
    return redirect(url_for("dashboard"))

@app.route("/run/<name>")
@login_required
def run_bot(name):
    success, msg = start_app(name)
    flash(msg, "success" if success else "error")
    return redirect(url_for("dashboard"))

@app.route("/stop/<name>")
@login_required
def stop_bot(name):
    if stop_app(name):
        flash("Stopped successfully", "success")
    else:
        flash("Not running", "info")
    return redirect(url_for("dashboard"))

@app.route("/restart/<name>")
@login_required
def restart_bot(name):
    success, msg = restart_app(name)
    flash(msg, "success" if success else "error")
    return redirect(url_for("dashboard"))

@app.route("/delete/<name>")
@login_required
def delete_bot(name):
    stop_app(name)
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name)
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir, ignore_errors=True)
        configs = load_startup_configs()
        if name in configs:
            del configs[name]
            save_startup_configs(configs)
        flash("Deleted successfully", "success")
    return redirect(url_for("dashboard"))

@app.route("/console/<name>")
@login_required
def console_page(name):
    return render_template_string(CONSOLE_TEMPLATE, bot_name=name)

@app.route("/console/<name>/stream")
@login_required
def console_stream(name):
    key = (USER, name)
    def generate():
        last_len = 0
        while True:
            try:
                if key in process_output and key in process_locks:
                    with process_locks[key]:
                        current = process_output[key]
                        if len(current) > last_len:
                            new_lines = current[last_len:]
                            yield f"data: {json.dumps({'lines': new_lines})}\n\n"
                            last_len = len(current)
            except Exception as e:
                print(f"Stream error: {e}")
            time.sleep(0.1)
    return Response(generate(), mimetype='text/event-stream')

@app.route("/console/<name>/input", methods=["POST"])
@login_required
def console_input(name):
    key = (USER, name)
    data = request.json
    command = data.get('command', '')
    if key in processes:
        p = processes[key]
        try:
            if p.poll() is None:
                p.stdin.write(command + '\n')
                p.stdin.flush()
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": "Process stopped"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False, "error": "Process not found"})

@app.route("/files/<name>")
@login_required
def file_manager(name):
    path = request.args.get('path', '')
    path = path.replace('..', '').replace('//', '/').strip('/')
    items = get_directory_structure(name, path)
    return render_template_string(FILE_MANAGER_TEMPLATE,
                                 bot_name=name,
                                 items=items,
                                 current_path=path)

@app.route("/files/<name>/upload_file", methods=["POST"])
@login_required
def upload_file(name):
    path = request.form.get('path', '')
    path = path.replace('..', '').replace('//', '/').strip('/')
    file = request.files.get('file')
    if file:
        app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
        full_path = os.path.join(app_dir, path, file.filename)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            file.save(full_path)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": False, "error": "No file"})

@app.route("/files/<name>/delete_file", methods=["POST"])
@login_required
def delete_file(name):
    data = request.json
    filepath = data.get('path', '')
    filepath = filepath.replace('..', '').replace('//', '/').strip('/')
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
    full_path = os.path.join(app_dir, filepath)
    if not full_path.startswith(app_dir):
        return jsonify({"success": False, "error": "Invalid path"})
    try:
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/files/<name>/rename_file", methods=["POST"])
@login_required
def rename_file(name):
    data = request.json
    old_path = data.get('old_path', '')
    new_name = data.get('new_name', '')
    old_path = old_path.replace('..', '').replace('//', '/').strip('/')
    new_name = new_name.replace('..', '').replace('/', '').strip()
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
    old_full = os.path.join(app_dir, old_path)
    new_full = os.path.join(os.path.dirname(old_full), new_name)
    if not old_full.startswith(app_dir) or not new_full.startswith(app_dir):
        return jsonify({"success": False, "error": "Invalid path"})
    try:
        os.rename(old_full, new_full)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/files/<name>/mkdir", methods=["POST"])
@login_required
def create_folder(name):
    data = request.json
    path = data.get('path', '')
    folder_name = data.get('name', '')
    path = path.replace('..', '').replace('//', '/').strip('/')
    folder_name = folder_name.replace('..', '').replace('/', '').strip()
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
    full_path = os.path.join(app_dir, path, folder_name)
    if not full_path.startswith(app_dir):
        return jsonify({"success": False, "error": "Invalid path"})
    try:
        os.makedirs(full_path, exist_ok=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/files/<name>/edit")
@login_required
def edit_file_page(name):
    filepath = request.args.get('path', '')
    filepath = filepath.replace('..', '').replace('//', '/').strip('/')
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
    full_path = os.path.join(app_dir, filepath)
    if not full_path.startswith(app_dir):
        return "Invalid path", 403
    content = ""
    if os.path.exists(full_path) and os.path.isfile(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            content = f"Error: {str(e)}"
    return render_template_string(EDIT_FILE_TEMPLATE,
                                 bot_name=name,
                                 filepath=filepath,
                                 content=content)

@app.route("/files/<name>/save", methods=["POST"])
@login_required
def save_file(name):
    data = request.json
    filepath = data.get('path', '')
    content = data.get('content', '')
    filepath = filepath.replace('..', '').replace('//', '/').strip('/')
    app_dir = os.path.join(UPLOAD_FOLDER, USER, name, "extracted")
    full_path = os.path.join(app_dir, filepath)
    if not full_path.startswith(app_dir):
        return jsonify({"success": False, "error": "Invalid path"})
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/set_startup/<name>", methods=["POST"])
@login_required
def set_startup(name):
    filename = request.form.get('startup_file')
    if filename:
        set_startup_file(name, filename)
        flash(f"Startup file set to {filename}", "success")
    return redirect(url_for('dashboard'))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------- UPDATED TEMPLATES WITH MOBILE-FIRST DARK UI ----------
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Login</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0b0f14;
            color: #e6edf3;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-box {
            background: #161b22;
            padding: 30px 25px;
            border-radius: 16px;
            max-width: 400px;
            width: 100%;
            border: 1px solid #30363d;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .login-box h2 {
            font-weight: 600;
            font-size: 24px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .login-box h2:before {
            content: "🔐";
        }
        input {
            width: 100%;
            padding: 14px 16px;
            margin: 8px 0 16px;
            border-radius: 8px;
            border: 1px solid #30363d;
            background: #0d1117;
            color: #e6edf3;
            font-size: 16px;
            transition: border 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #58a6ff;
        }
        button {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 8px;
            background: #238636;
            color: white;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        button:hover { background: #2ea043; }
        .error {
            color: #f85149;
            margin-bottom: 12px;
            font-size: 14px;
        }
    </style>
</head>
<body>
<div class="login-box">
    <h2>Control Panel</h2>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
    <form method="post">
        <input type="password" name="password" placeholder="Enter password" required>
        <button type="submit">Login</button>
    </form>
</div>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Dashboard</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0b0f14;
            color: #e6edf3;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            padding: 16px;
            padding-bottom: 40px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 0;
            border-bottom: 1px solid #21262d;
            margin-bottom: 24px;
        }
        .header h1 {
            font-size: 22px;
            font-weight: 600;
            letter-spacing: -0.5px;
        }
        .header h1 span { color: #58a6ff; }
        .btn {
            display: inline-block;
            padding: 8px 18px;
            border-radius: 8px;
            border: none;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s;
            background: #21262d;
            color: #e6edf3;
        }
        .btn-primary { background: #238636; color: white; }
        .btn-primary:hover { background: #2ea043; }
        .btn-danger { background: #da3633; color: white; }
        .btn-danger:hover { background: #f85149; }
        .btn-warning { background: #d29922; color: white; }
        .btn-warning:hover { background: #e3b341; }
        .btn-sm { padding: 4px 12px; font-size: 12px; }
        .btn-outline { background: transparent; border: 1px solid #30363d; }
        .btn-outline:hover { background: #21262d; }

        .upload-area {
            background: #161b22;
            padding: 20px;
            border-radius: 12px;
            border: 1px solid #30363d;
            margin-bottom: 24px;
        }
        .upload-area h3 {
            font-size: 16px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .upload-area form {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }
        .upload-area input[type="file"] {
            flex: 1;
            min-width: 150px;
            padding: 10px;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            color: #e6edf3;
            font-size: 14px;
        }
        .upload-area button { padding: 12px 24px; }

        .flash {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-size: 14px;
        }
        .flash-success { background: #1b3a2a; border-left: 4px solid #3fb950; }
        .flash-error { background: #3a1a1a; border-left: 4px solid #f85149; }
        .flash-info { background: #1a2a3a; border-left: 4px solid #58a6ff; }

        .bot-card {
            background: #161b22;
            border-radius: 12px;
            border: 1px solid #30363d;
            padding: 16px;
            margin-bottom: 16px;
            transition: border 0.2s;
        }
        .bot-card:hover { border-color: #58a6ff; }
        .bot-top {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 8px;
        }
        .bot-name {
            font-size: 18px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .status-badge {
            display: inline-block;
            padding: 2px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
            text-transform: uppercase;
        }
        .status-running { background: #238636; color: white; }
        .status-stopped { background: #484f58; color: #b1bac4; }
        .bot-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin: 10px 0 6px;
        }
        .bot-log {
            background: #0d1117;
            padding: 10px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 13px;
            line-height: 1.5;
            max-height: 120px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            border: 1px solid #21262d;
            margin-top: 8px;
            color: #b1bac4;
        }
        .bot-log::-webkit-scrollbar { width: 6px; }
        .bot-log::-webkit-scrollbar-track { background: #0d1117; }
        .bot-log::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }

        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #8b949e;
        }
        .empty-state p { margin-top: 10px; }

        @media (max-width: 600px) {
            .header h1 { font-size: 18px; }
            .bot-top { flex-direction: column; align-items: flex-start; }
            .bot-actions { width: 100%; justify-content: flex-start; }
            .upload-area form { flex-direction: column; align-items: stretch; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🤖 <span>My</span> vps</h1>
        <a href="/logout" class="btn btn-danger btn-sm">Logout</a>
    </div>

    <div class="upload-area">
        <h3>📦 Upload New Bot (ZIP)</h3>
        <form method="post" action="/upload" enctype="multipart/form-data">
            <input type="file" name="file" accept=".zip" required>
            <button type="submit" class="btn btn-primary">Upload</button>
        </form>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, msg in messages %}
          <div class="flash flash-{{ category }}">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    {% for bot in apps %}
    <div class="bot-card">
        <div class="bot-top">
            <span class="bot-name">
                {{ bot.name }}
                <span class="status-badge status-{{ 'running' if bot.running else 'stopped' }}">
                    {{ 'Running' if bot.running else 'Stopped' }}
                </span>
            </span>
            <span style="font-size:13px; color:#8b949e;">startup: {{ bot.startup_file }}</span>
        </div>
        <div class="bot-actions">
            <a href="/run/{{ bot.name }}" class="btn btn-primary btn-sm">▶ Start</a>
            <a href="/stop/{{ bot.name }}" class="btn btn-warning btn-sm">⏹ Stop</a>
            <a href="/restart/{{ bot.name }}" class="btn btn-sm">🔄 Restart</a>
            <a href="/console/{{ bot.name }}" class="btn btn-sm">📟 Console</a>
            <a href="/files/{{ bot.name }}" class="btn btn-sm">📁 Files</a>
            <a href="/delete/{{ bot.name }}" class="btn btn-danger btn-sm" onclick="return confirm('Delete this bot?')">🗑 Delete</a>
        </div>
        <div class="bot-log">{{ bot.log }}</div>
    </div>
    {% else %}
    <div class="empty-state">
        <div style="font-size:48px;">📭</div>
        <p>No bots uploaded yet. Upload a ZIP to get started.</p>
    </div>
    {% endfor %}
</div>
</body>
</html>
"""

CONSOLE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Console - {{ bot_name }}</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0b0f14;
            color: #e6edf3;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            padding: 16px;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            width: 100%;
            display: flex;
            flex-direction: column;
            flex: 1;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 12px;
            border-bottom: 1px solid #21262d;
            flex-wrap: wrap;
            gap: 8px;
        }
        .header h1 {
            font-size: 20px;
            font-weight: 600;
        }
        .header a {
            color: #58a6ff;
            text-decoration: none;
            font-size: 14px;
        }
        .header a:hover { text-decoration: underline; }
        .console-output {
            flex: 1;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 14px;
            margin: 12px 0 8px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 14px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-all;
            min-height: 200px;
            color: #e6edf3;
        }
        .console-output::-webkit-scrollbar { width: 6px; }
        .console-output::-webkit-scrollbar-track { background: #0d1117; }
        .console-output::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }

        .input-area {
            display: flex;
            gap: 8px;
            margin-top: 8px;
            padding: 4px 0;
        }
        .input-area input {
            flex: 1;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #30363d;
            background: #161b22;
            color: #e6edf3;
            font-size: 16px;
            font-family: inherit;
        }
        .input-area input:focus {
            outline: none;
            border-color: #58a6ff;
        }
        .input-area button {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            background: #238636;
            color: white;
            font-weight: 600;
            font-size: 16px;
            cursor: pointer;
        }
        .input-area button:hover { background: #2ea043; }
        @media (max-width: 480px) {
            .header h1 { font-size: 17px; }
            .console-output { font-size: 13px; padding: 10px; }
            .input-area input { font-size: 15px; padding: 10px 14px; }
            .input-area button { padding: 10px 16px; font-size: 15px; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📟 Console: {{ bot_name }}</h1>
        <a href="/dashboard">⬅ Dashboard</a>
    </div>
    <div class="console-output" id="output">Waiting for output...</div>
    <div class="input-area">
        <input type="text" id="cmdInput" placeholder="Type command..." autofocus>
        <button onclick="sendCommand()">Send</button>
    </div>
</div>
<script>
const botName = "{{ bot_name }}";
const output = document.getElementById('output');

const evtSource = new EventSource('/console/' + botName + '/stream');
evtSource.onmessage = function(event) {
    const data = JSON.parse(event.data);
    if (data.lines) {
        output.textContent += data.lines.join('');
        output.scrollTop = output.scrollHeight;
    }
};
evtSource.onerror = function(e) {
    console.log('SSE error', e);
};

function sendCommand() {
    const input = document.getElementById('cmdInput');
    const cmd = input.value.trim();
    if (!cmd) return;
    fetch('/console/' + botName + '/input', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command: cmd})
    }).then(res => res.json()).then(data => {
        if (!data.success) alert('Error: ' + data.error);
        input.value = '';
        input.focus();
    }).catch(err => alert('Error: ' + err));
}
document.getElementById('cmdInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') sendCommand();
});
</script>
</body>
</html>
"""

FILE_MANAGER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Files - {{ bot_name }}</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0b0f14;
            color: #e6edf3;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            padding: 16px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            padding-bottom: 12px;
            border-bottom: 1px solid #21262d;
            margin-bottom: 16px;
        }
        .header h1 { font-size: 20px; font-weight: 600; }
        .header a { color: #58a6ff; text-decoration: none; font-size: 14px; }
        .header a:hover { text-decoration: underline; }

        .toolbar {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 16px;
            align-items: center;
            background: #161b22;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid #30363d;
        }
        .toolbar form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; flex:1; }
        .toolbar input[type="file"] {
            padding: 8px;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #e6edf3;
            font-size: 13px;
            flex:1;
            min-width: 120px;
        }
        .btn {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 6px;
            border: none;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            text-decoration: none;
            background: #21262d;
            color: #e6edf3;
            transition: background 0.15s;
        }
        .btn-primary { background: #238636; color: white; }
        .btn-primary:hover { background: #2ea043; }
        .btn-danger { background: #da3633; color: white; }
        .btn-danger:hover { background: #f85149; }
        .btn-sm { padding: 4px 10px; font-size: 12px; }
        .btn-outline { background: transparent; border: 1px solid #30363d; }
        .btn-outline:hover { background: #21262d; }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        th {
            text-align: left;
            padding: 10px 8px;
            border-bottom: 2px solid #21262d;
            color: #8b949e;
            font-weight: 500;
        }
        td {
            padding: 8px 8px;
            border-bottom: 1px solid #21262d;
            vertical-align: middle;
        }
        .file-row:hover { background: #161b22; }
        .file-icon { margin-right: 6px; }
        .file-link { color: #58a6ff; text-decoration: none; }
        .file-link:hover { text-decoration: underline; }
        .folder-link { color: #d29922; }
        .actions { display: flex; gap: 4px; flex-wrap: wrap; }

        .empty-folder { text-align: center; padding: 30px 0; color: #8b949e; }
        .back-link { display: inline-block; margin-bottom: 12px; }

        @media (max-width: 600px) {
            .toolbar { flex-direction: column; align-items: stretch; }
            .toolbar form { flex-direction: column; align-items: stretch; }
            table, thead, tbody, th, td, tr { display: block; }
            th { display: none; }
            td { border: none; padding: 8px 4px; border-bottom: 1px solid #21262d; }
            .file-row { display: block; }
            .actions { justify-content: flex-start; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📁 Files: {{ bot_name }}</h1>
        <div>
            <a href="/dashboard">Dashboard</a> | <a href="/console/{{ bot_name }}">Console</a>
        </div>
    </div>

    <div class="toolbar">
        <form method="post" action="/files/{{ bot_name }}/upload_file" enctype="multipart/form-data">
            <input type="hidden" name="path" value="{{ current_path }}">
            <input type="file" name="file" required>
            <button type="submit" class="btn btn-primary">Upload</button>
        </form>
        <button onclick="createFolder()" class="btn btn-outline">📂 New Folder</button>
    </div>

    <table>
        <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th>Actions</th></tr></thead>
        <tbody>
        {% if current_path %}
        <tr class="file-row">
            <td colspan="4">
                <a href="?path={{ current_path.rsplit('/',1)[0] if '/' in current_path else '' }}" class="file-link">📂 ..</a>
            </td>
        </tr>
        {% endif %}
        {% for item in items %}
        <tr class="file-row">
            <td>
                {% if item.is_dir %}📁 {% else %}📄 {% endif %}
                <a href="{% if item.is_dir %}?path={{ item.path }}{% else %}/files/{{ bot_name }}/edit?path={{ item.path }}{% endif %}"
                   class="file-link {% if item.is_dir %}folder-link{% endif %}">
                    {{ item.name }}
                </a>
            </td>
            <td>{{ item.size if not item.is_dir else '-' }}</td>
            <td>{{ item.modified }}</td>
            <td class="actions">
                {% if not item.is_dir %}
                <a href="/files/{{ bot_name }}/edit?path={{ item.path }}" class="btn btn-sm">✏️</a>
                {% endif %}
                <button onclick="renameItem('{{ item.path }}')" class="btn btn-sm">🔄</button>
                <button onclick="deleteItem('{{ item.path }}')" class="btn btn-danger btn-sm">🗑</button>
            </td>
        </tr>
        {% else %}
        <tr><td colspan="4" class="empty-folder">📭 This folder is empty</td></tr>
        {% endfor %}
        </tbody>
    </table>
</div>
<script>
function deleteItem(path) {
    if (!confirm('Delete '+path+'?')) return;
    fetch('/files/{{ bot_name }}/delete_file', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: path})
    }).then(res => res.json()).then(data => {
        if (data.success) location.reload();
        else alert('Error: '+data.error);
    });
}
function renameItem(oldPath) {
    const newName = prompt('Enter new name:', oldPath.split('/').pop());
    if (!newName) return;
    fetch('/files/{{ bot_name }}/rename_file', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({old_path: oldPath, new_name: newName})
    }).then(res => res.json()).then(data => {
        if (data.success) location.reload();
        else alert('Error: '+data.error);
    });
}
function createFolder() {
    const name = prompt('Enter folder name:');
    if (!name) return;
    fetch('/files/{{ bot_name }}/mkdir', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: '{{ current_path }}', name: name})
    }).then(res => res.json()).then(data => {
        if (data.success) location.reload();
        else alert('Error: '+data.error);
    });
}
</script>
</body>
</html>
"""

EDIT_FILE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Edit - {{ filepath }}</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background: #0b0f14;
            color: #e6edf3;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
            padding: 16px;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .container {
            max-width: 1100px;
            margin: 0 auto;
            width: 100%;
            display: flex;
            flex-direction: column;
            flex: 1;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            padding-bottom: 12px;
            border-bottom: 1px solid #21262d;
            margin-bottom: 16px;
        }
        .header h1 { font-size: 18px; font-weight: 600; }
        .header a { color: #58a6ff; text-decoration: none; font-size: 14px; }
        .header a:hover { text-decoration: underline; }

        textarea {
            flex: 1;
            width: 100%;
            padding: 14px;
            background: #0d1117;
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 8px;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 15px;
            line-height: 1.6;
            resize: vertical;
            min-height: 300px;
        }
        textarea:focus { outline: none; border-color: #58a6ff; }

        .actions {
            display: flex;
            gap: 12px;
            margin-top: 12px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 10px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 16px;
            cursor: pointer;
            background: #238636;
            color: white;
        }
        .btn:hover { background: #2ea043; }
        .btn-danger { background: #da3633; }
        .btn-danger:hover { background: #f85149; }

        @media (max-width: 480px) {
            .header h1 { font-size: 16px; }
            textarea { font-size: 14px; padding: 10px; }
            .btn { padding: 8px 16px; font-size: 14px; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>✏️ Editing: {{ filepath }}</h1>
        <a href="/files/{{ bot_name }}">⬅ Back to files</a>
    </div>
    <textarea id="content">{{ content }}</textarea>
    <div class="actions">
        <button onclick="saveFile()" class="btn">💾 Save</button>
        <button onclick="window.location.href='/files/{{ bot_name }}'" class="btn btn-danger">Cancel</button>
    </div>
</div>
<script>
function saveFile() {
    const content = document.getElementById('content').value;
    fetch('/files/{{ bot_name }}/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: '{{ filepath }}', content: content})
    }).then(res => res.json()).then(data => {
        if (data.success) alert('Saved!');
        else alert('Error: '+data.error);
    });
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)