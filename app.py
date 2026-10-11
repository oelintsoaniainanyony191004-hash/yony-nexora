from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import os
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature

app = Flask(__name__)
import os
app.secret_key = os.environ.get("YONY_SECRET_KEY")

UPLOAD_PROFILE = "static/uploads/profile"
UPLOAD_VIDEO = "static/uploads/videos"

os.makedirs(UPLOAD_PROFILE, exist_ok=True)
os.makedirs(UPLOAD_VIDEO, exist_ok=True)



class PostgreSQLCompat:
    def __init__(self, conn):
        self.conn = conn
        self.row_factory = None

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                          "SERIAL PRIMARY KEY")
        sql = sql.replace("INSERT OR IGNORE INTO",
                          "INSERT INTO")
        if "INSERT INTO" in sql and "ON CONFLICT" not in sql:
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        except Exception as e:
            cur.close()
            if getattr(e, "pgcode", None) == "23505":
                raise sqlite3.IntegrityError(str(e)) from e
            raise
        return cur

    def cursor(self):
        return PostgreSQLCursor(self)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class PostgreSQLCursor:
    def __init__(self, db):
        self.db = db
        self.cur = db.conn.cursor()
        self.fake_rows = None

    def execute(self, sql, params=()):
        if "PRAGMA table_info(user_settings)" in sql:
            self.cur.execute("""
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'user_settings'
                ORDER BY ordinal_position
            """)
            return self
        sql = sql.replace("?", "%s")
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "INSERT INTO" in sql and "ON CONFLICT" not in sql:
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        self.cur.execute(sql, params)
        return self

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def close(self):
        self.cur.close()


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return sqlite3.connect("social.db")
    import psycopg2
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(url, cursor_factory=DictCursor)
    return PostgreSQLCompat(conn)

def init_interaction_tables():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            profile_pic TEXT,
            email TEXT,
            phone TEXT,
            bio TEXT,
            cover_pic TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, username)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()
    db.close()


init_interaction_tables()



@app.route("/search_users")
def search_users():
    if "username" not in session:
        return jsonify([])

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    db = get_db()
    rows = db.execute(
        "SELECT username, profile_pic FROM users "
        "WHERE username LIKE ? "
        "ORDER BY username LIMIT 10",
        ("%" + q + "%",)
    ).fetchall()
    db.close()

    return jsonify([
        {"username": row[0], "profile_pic": row[1]}
        for row in rows
    ])


@app.route("/user/<username>")
def view_user(username):
    if "username" not in session:
        return redirect("/login")

    db = get_db()
    row = db.execute(
        "SELECT username, profile_pic, bio, cover_pic "
        "FROM users WHERE username=?",
        (username,)
    ).fetchone()
    db.close()

    if not row:
        return "Kaonty tsy hita.", 404

    user = {
        "username": row[0],
        "profile_pic": row[1],
        "bio": row[2],
        "cover_pic": row[3]
    }
    return render_template("user_profile.html", user=user)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session:
        return redirect("/login")

    db = get_db()

    if request.method == "POST":
        username = session["username"]

        bio = request.form.get("bio", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()

        db.execute(
            "UPDATE users SET bio=?, email=?, phone=? WHERE username=?",
            (bio, email, phone, username)
        )

        profile_file = request.files.get("profile_pic")
        cover_file = request.files.get("cover_pic")

        if profile_file and profile_file.filename:
            os.makedirs(UPLOAD_PROFILE, exist_ok=True)
            filename = secure_filename(profile_file.filename)

            if filename:
                filename = username + "_" + filename
                profile_file.save(os.path.join(UPLOAD_PROFILE, filename))

                db.execute(
                    "UPDATE users SET profile_pic=? WHERE username=?",
                    (filename, username)
                )

        if cover_file and cover_file.filename:
            os.makedirs("static/uploads/cover", exist_ok=True)
            filename = secure_filename(cover_file.filename)

            if filename:
                filename = username + "_" + filename
                cover_file.save(
                    os.path.join("static/uploads/cover", filename)
                )

                db.execute(
                    "UPDATE users SET cover_pic=? WHERE username=?",
                    (filename, username)
                )

        db.commit()
        db.close()
        return redirect("/profile")

    user = db.execute(
        "SELECT username, profile_pic, email, phone, bio, cover_pic "
        "FROM users WHERE username=?",
        (session["username"],)
    ).fetchone()

    db.close()

    if not user:
        return redirect("/login")

    user = {
        "username": user[0],
        "profile_pic": user[1],
        "email": user[2],
        "phone": user[3],
        "bio": user[4],
        "cover_pic": user[5]
    }

    return render_template("profile.html", user=user)


    
        
@app.route("/")
def index():
    if "username" not in session:
        return redirect("/login")
    db = get_db()

    user = db.execute(
        "SELECT username, profile_pic FROM users WHERE username=?",
        (session["username"],)
    ).fetchone()

    posts = db.execute("SELECT p.id, p.username, p.content, p.created_at, (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id), (SELECT COUNT(*) FROM shares sh WHERE sh.post_id=p.id), (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id), EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.username=?) FROM posts p ORDER BY p.id DESC", (session["username"],)).fetchall()

    db.close()

    return render_template(
        "index.html",
        username=session["username"],
        profile_pic=user[1] if user else None,
        posts=posts
    )



@app.route("/friends")
def friends():
    if "username" not in session:
        return redirect("/login")
    return render_template("page.html", title="Amis", icon="👥")

@app.route("/add")
def add():
    if "username" not in session:
        return redirect("/login")
    return render_template("page.html", title="Ajouter", icon="👤➕")

@app.route("/messages")
def messages():
    if "username" not in session:
        return redirect("/login")
    return render_template("page.html", title="Messages", icon="📩")

@app.route("/notifications")
def notifications():
    if "username" not in session:
        return redirect("/login")
    return render_template("page.html", title="Notifications", icon="🔔")

@app.route("/videos")
def videos():
    if "username" not in session:
        return redirect("/login")
    return render_template("page.html", title="Vidéos", icon="🎬")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        contact = request.form["contact"].strip()
        password = request.form["password"]

        db = get_db()

        email = None
        phone = None

        if "@" in contact:
            email = contact
        else:
            phone = contact

        try:
            db.execute(
                """INSERT INTO users
                   (username, password, profile_pic, email, phone)
                   VALUES (?, ?, ?, ?, ?)""",
                (username, password, None, email, phone)
            )
            db.commit()
            db.close()
            return redirect("/login")

        except sqlite3.IntegrityError:
            db.close()
            return "Username, email na numéro efa ampiasaina."

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form["username"]
        password = request.form["password"]

        db = get_db()
        user = db.execute(
            """SELECT * FROM users
               WHERE (username=? OR email=? OR phone=?)
               AND password=?""",
            (identifier, identifier, identifier, password)
        ).fetchone()
        db.close()

        if user:
            session["username"] = user[1]
            response = redirect("/")
            token = URLSafeTimedSerializer(app.secret_key).dumps(user[1])
            response.set_cookie(
                "yony_remember", token, max_age=2592000,
                httponly=True, secure=request.is_secure, samesite="Lax"
            )
            return response

        return "Adresse email, numéro ou mot de passe diso."

    token = request.cookies.get("yony_remember")
    if token:
        try:
            username = URLSafeTimedSerializer(app.secret_key).loads(
                token, max_age=2592000
            )
            db = get_db()
            user = db.execute(
                "SELECT username FROM users WHERE username=?", (username,)
            ).fetchone()
            db.close()
            if user:
                session["username"] = username
                return redirect("/")
        except (BadSignature, Exception):
            pass

    db = get_db()
    saved_accounts = db.execute(
        "SELECT username, email, phone, profile_pic FROM users ORDER BY id DESC"
    ).fetchall()
    db.close()
    return render_template("login.html", saved_accounts=saved_accounts)


@app.route("/post", methods=["POST"])
def create_post():
    if "username" not in session:
        return redirect("/login")

    content = request.form.get("content", "").strip()

    if content:
        db = get_db()

        db.execute(
            "INSERT INTO posts (username, content) VALUES (?, ?)",
            (session["username"], content)
        )

        db.commit()
        db.close()

    return redirect("/")


@app.route("/upload_video", methods=["POST"])
def upload_video():
    if "username" not in session:
        return redirect("/login")

    video = request.files.get("video")

    if video and video.filename:
        filename = secure_filename(video.filename)
        filename = session["username"] + "_" + filename

        path = os.path.join(UPLOAD_VIDEO, filename)
        video.save(path)

    return redirect("/")




@app.route("/like/<int:post_id>", methods=["POST"])
def like_post(post_id):
    if "username" not in session:
        return jsonify(success=False, message="Mila miditra aloha."), 401
    db = get_db()
    if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        db.close()
        return jsonify(success=False, message="Tsy hita ny publication."), 404
    username = session["username"]
    existing = db.execute("SELECT id FROM likes WHERE post_id=? AND username=?", (post_id, username)).fetchone()
    if existing:
        db.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, username))
        liked = False
    else:
        db.execute("INSERT INTO likes (post_id, username) VALUES (?, ?)", (post_id, username))
        liked = True
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM likes WHERE post_id=?", (post_id,)).fetchone()[0]
    db.close()
    return jsonify(success=True, liked=liked, count=count)


@app.route("/comments/<int:post_id>", methods=["GET"])
def get_comments(post_id):
    if "username" not in session:
        return jsonify(comments=[]), 401
    db = get_db()
    rows = db.execute("SELECT username, content, created_at FROM comments WHERE post_id=? ORDER BY id", (post_id,)).fetchall()
    db.close()
    return jsonify(comments=[{"username": r[0], "content": r[1], "created_at": r[2]} for r in rows])


@app.route("/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id):
    if "username" not in session:
        return jsonify(success=False, message="Mila miditra aloha."), 401
    content = request.form.get("content", "").strip()
    if not content:
        return jsonify(success=False, message="Soraty aloha ny commentaire.")
    db = get_db()
    if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        db.close()
        return jsonify(success=False, message="Tsy hita ny publication."), 404
    username = session["username"]
    db.execute("INSERT INTO comments (post_id, username, content) VALUES (?, ?, ?)", (post_id, username, content))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM comments WHERE post_id=?", (post_id,)).fetchone()[0]
    db.close()
    return jsonify(success=True, username=username, content=content, count=count)


@app.route("/share/<int:post_id>", methods=["POST"])
def share_post(post_id):
    if "username" not in session:
        return jsonify(success=False, message="Mila miditra aloha."), 401
    db = get_db()
    if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        db.close()
        return jsonify(success=False, message="Tsy hita ny publication."), 404
    db.execute("INSERT INTO shares (post_id, username) VALUES (?, ?)", (post_id, session["username"]))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM shares WHERE post_id=?", (post_id,)).fetchone()[0]
    db.close()
    return jsonify(success=True, count=count)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")



@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "username" not in session:
        return redirect("/login")

    username = session["username"]
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            username TEXT PRIMARY KEY,
            allow_profile_screenshots INTEGER DEFAULT 1,
            profile_visible INTEGER DEFAULT 1,
            notifications_enabled INTEGER DEFAULT 1
        )
    """)

    cur.execute("PRAGMA table_info(user_settings)")
    columns = [row["name"] for row in cur.fetchall()]

    for column in ("allow_profile_screenshots", "profile_visible",
                   "notifications_enabled"):
        if column not in columns:
            cur.execute(
                f"ALTER TABLE user_settings ADD COLUMN {column} INTEGER DEFAULT 1"
            )

    cur.execute(
        "INSERT OR IGNORE INTO user_settings (username) VALUES (?)",
        (username,)
    )

    message = ""
    message_type = "success"

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "account":
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()

            cur.execute(
                "SELECT username FROM users WHERE (email=? OR phone=?) AND username!=?",
                (email, phone, username)
            )
            duplicate = cur.fetchone()

            if duplicate:
                message = "Efa ampiasain'ny kaonty hafa io email na téléphone io."
                message_type = "error"
            else:
                cur.execute(
                    "UPDATE users SET email=?, phone=? WHERE username=?",
                    (email, phone, username)
                )
                message = "Voatahiry ny mombamomba ny kaontinao."

        elif action == "password":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            cur.execute("SELECT password FROM users WHERE username=?", (username,))
            user = cur.fetchone()

            if not user or user["password"] != current:
                message = "Diso ny mot de passe actuel."
                message_type = "error"
            elif len(new) < 6:
                message = "Tokony ahitana tarehintsoratra 6 farafahakeliny ny mot de passe vaovao."
                message_type = "error"
            elif new != confirm:
                message = "Tsy mitovy ny mot de passe vaovao sy ny confirmation."
                message_type = "error"
            else:
                cur.execute(
                    "UPDATE users SET password=? WHERE username=?",
                    (new, username)
                )
                message = "Niova ny mot de passe."

        elif action == "privacy":
            screenshots = 1 if request.form.get("allow_profile_screenshots") else 0
            visible = 1 if request.form.get("profile_visible") else 0
            cur.execute("""
                UPDATE user_settings
                SET allow_profile_screenshots=?, profile_visible=?
                WHERE username=?
            """, (screenshots, visible, username))
            message = "Voatahiry ny réglages de confidentialité."

        elif action == "notifications":
            enabled = 1 if request.form.get("notifications_enabled") else 0
            cur.execute("""
                UPDATE user_settings SET notifications_enabled=?
                WHERE username=?
            """, (enabled, username))
            message = "Voatahiry ny réglages notifications."

    conn.commit()

    cur.execute("SELECT email, phone FROM users WHERE username=?", (username,))
    user = cur.fetchone()

    cur.execute("SELECT * FROM user_settings WHERE username=?", (username,))
    prefs = cur.fetchone()
    conn.close()

    return render_template(
        "settings.html",
        email=user["email"] or "",
        phone=user["phone"] or "",
        allow_profile_screenshots=prefs["allow_profile_screenshots"],
        profile_visible=prefs["profile_visible"],
        notifications_enabled=prefs["notifications_enabled"],
        message=message,
        message_type=message_type
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
