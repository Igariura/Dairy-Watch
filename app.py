from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)
from dotenv import load_dotenv
from openai import OpenAI
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
import os
import tempfile
from datetime import datetime, timedelta 
from flask import Flask, request, jsonify, render_template, Response, send_from_directory   

load_dotenv()

app = Flask(__name__)

VIDEO_FEEDS_DIR = os.path.join(os.path.dirname(__file__), "static", "video_feeds")
os.makedirs(VIDEO_FEEDS_DIR, exist_ok=True)

# ── Database ──────────────────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"]    = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ── JWT ───────────────────────────────────────────
app.config["JWT_SECRET_KEY"]             = os.getenv("JWT_SECRET_KEY")
app.config["JWT_TOKEN_LOCATION"]         = ["headers"]
app.config["JWT_COOKIE_SECURE"]          = False
app.config["JWT_COOKIE_CSRF_PROTECT"]    = False
app.config["JWT_ACCESS_COOKIE_NAME"]     = "access_token"
app.config["JWT_ACCESS_COOKIE_PATH"]     = "/"
app.config["JWT_COOKIE_SAMESITE"]        = "Lax"
app.config["JWT_HEADER_NAME"]            = "Authorization"
app.config["JWT_HEADER_TYPE"]            = "Bearer"
app.config["JWT_ACCESS_TOKEN_EXPIRES"]   = timedelta(hours=8)

# ── Email ─────────────────────────────────────────
app.config["MAIL_SERVER"]                = "smtp.gmail.com"
app.config["MAIL_PORT"]                  = 587
app.config["MAIL_USE_TLS"]               = True
app.config["MAIL_USERNAME"]              = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"]              = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"]        = os.getenv("MAIL_DEFAULT_SENDER")

# ── File uploads ──────────────────────────────────
app.config["MAX_CONTENT_LENGTH"]         = 50 * 1024 * 1024
ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp"}
ALLOWED_VIDEOS = {"mp4", "mov", "avi", "mkv"}

def allowed_file(filename, allowed):
    return "." in filename and \
        filename.rsplit(".", 1)[1].lower() in allowed

# ── Extensions ────────────────────────────────────
db      = SQLAlchemy(app)
migrate = Migrate(app, db)
jwt     = JWTManager(app)
mail    = Mail(app)

# ── JWT error handlers ────────────────────────────
@jwt.unauthorized_loader
def unauthorized_callback(error):
    return jsonify({"error": "Missing or invalid token"}), 401

@jwt.invalid_token_loader
def invalid_token_callback(error):
    return jsonify({"error": "Invalid token"}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_data):
    return jsonify({"error": "Token has expired"}), 401

# ── OpenAI / Grok client ──────────────────────────
ai_client = OpenAI(
    api_key=os.getenv("GROK_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

print(f"GROK KEY: {os.getenv('GROK_API_KEY', 'NOT FOUND')[:15]}...")

def parse_ai_json(response):
    import json, re
    raw = response.choices[0].message.content.strip()
    print(f"RAW AI RESPONSE: {repr(raw[:200])}")
    # Strip markdown fences
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    # Find JSON object within response
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    return json.loads(raw.strip())

# ─────────────────────────────────────────────────
#  MODELS
# ─────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), unique=True, nullable=False)
    password   = db.Column(db.String(256), nullable=False)
    role       = db.Column(db.String(20), default="worker")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Cow(db.Model):
    __tablename__ = "cows"

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)  # ← add this
    name          = db.Column(db.String(100))
    tag_number    = db.Column(db.String(50), nullable=False)  # ← remove unique=True
    breed         = db.Column(db.String(100))
    date_of_birth = db.Column(db.Date)
    status        = db.Column(db.String(30), default="healthy")
    notes         = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            "id":         self.id,
            "name":       self.name,
            "tag_number": self.tag_number,
            "breed":      self.breed,
            "status":     self.status,
            "notes":      self.notes,
            "created_at": self.created_at.isoformat()
        }

class VideoFeed(db.Model):
    __tablename__ = "video_feeds"

    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    cow_id       = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)
    title        = db.Column(db.String(200))
    filename     = db.Column(db.String(200))
    ai_summary   = db.Column(db.Text)
    ai_concerns  = db.Column(db.Text)
    ai_recommendation = db.Column(db.Text)
    severity     = db.Column(db.String(20), default="normal")
    flagged      = db.Column(db.Boolean, default=False)
    recorded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":               self.id,
            "user_id":          self.user_id,
            "cow_id":           self.cow_id,
            "title":            self.title,
            "filename":         self.filename,
            "ai_summary":       self.ai_summary,
            "ai_concerns":      self.ai_concerns,
            "ai_recommendation": self.ai_recommendation,
            "severity":         self.severity,
            "flagged":          self.flagged,
            "recorded_at":      self.recorded_at.isoformat()
        }
        
        
class HealthRecord(db.Model):
    __tablename__ = "health_records"

    id                = db.Column(db.Integer, primary_key=True)
    cow_id            = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False)
    temperature       = db.Column(db.Float)
    is_limping        = db.Column(db.Boolean, default=False)
    is_lethargic      = db.Column(db.Boolean, default=False)
    is_not_eating     = db.Column(db.Boolean, default=False)
    ai_diagnosis      = db.Column(db.Text)
    ai_recommendation = db.Column(db.Text)
    flagged           = db.Column(db.Boolean, default=False)
    notes             = db.Column(db.Text)
    recorded_at       = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":                self.id,
            "cow_id":            self.cow_id,
            "temperature":       self.temperature,
            "is_limping":        self.is_limping,
            "is_lethargic":      self.is_lethargic,
            "is_not_eating":     self.is_not_eating,
            "ai_diagnosis":      self.ai_diagnosis,
            "ai_recommendation": self.ai_recommendation,
            "flagged":           self.flagged,
            "notes":             self.notes,
            "recorded_at":       self.recorded_at.isoformat()
        }


class MilkRecord(db.Model):
    __tablename__ = "milk_records"

    id           = db.Column(db.Integer, primary_key=True)
    cow_id       = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=False)
    session      = db.Column(db.String(20), nullable=False)
    yield_litres = db.Column(db.Float, nullable=False)
    recorded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":           self.id,
            "cow_id":       self.cow_id,
            "session":      self.session,
            "yield_litres": self.yield_litres,
            "recorded_at":  self.recorded_at.isoformat()
        }


class Alert(db.Model):
    __tablename__ = "alerts"

    id         = db.Column(db.Integer, primary_key=True)
    cow_id     = db.Column(db.Integer, db.ForeignKey("cows.id"), nullable=True)
    type       = db.Column(db.String(50))
    severity   = db.Column(db.String(20))
    title      = db.Column(db.String(200))
    message    = db.Column(db.Text)
    resolved   = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":         self.id,
            "cow_id":     self.cow_id,
            "type":       self.type,
            "severity":   self.severity,
            "title":      self.title,
            "message":    self.message,
            "resolved":   self.resolved,
            "created_at": self.created_at.isoformat()
        }


# ─────────────────────────────────────────────────
#  AI SERVICE
# ─────────────────────────────────────────────────

def analyze_cow_health(cow, data):
    prompt = f"""
You are a veterinary AI assistant for dairy cattle.
Analyze this cow's health data and respond with a diagnosis.

COW: {cow.name or "Unnamed"} (Tag: {cow.tag_number}, Breed: {cow.breed or "Unknown"})

READINGS:
- Temperature: {data.get("temperature", "not recorded")}°C  (normal: 38.0-39.5)
- Limping: {data.get("is_limping", False)}
- Lethargic: {data.get("is_lethargic", False)}
- Not eating: {data.get("is_not_eating", False)}
- Notes: {data.get("notes", "none")}

Respond ONLY in this exact JSON format, nothing else:
{{
  "diagnosis": "your diagnosis here",
  "recommendation": "what the farmer should do",
  "severity": "normal or warning or critical",
  "flagged": true or false
}}

flagged must be true if severity is warning or critical.
"""
    try:
        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": "You are a dairy cattle veterinary AI. Always respond with valid JSON only."},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300
        )
        import json
        return parse_ai_json(response)
        return json.loads(raw)

    except Exception as e:
        print(f"AI health analysis error: {e}")
        return {
            "diagnosis":      "AI analysis unavailable. Please check manually.",
            "recommendation": "Conduct a manual health check on this cow.",
            "severity":       "warning",
            "flagged":        True
        }


# ─────────────────────────────────────────────────
#  EMAIL SERVICE
# ─────────────────────────────────────────────────

def send_alert_email(title, message, severity, recipient=None, recommendation=None):
    try:
        if not recipient:
            recipient = os.getenv("MAIL_USERNAME")
        if not recipient:
            return

        emoji = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "🔔")

        recommendation_html = f"""
            <div style="margin-top:16px;padding:14px 16px;background:#e8f5ee;
                        border-radius:8px;border-left:4px solid #1a6b3c;">
                <p style="margin:0 0 6px 0;font-size:12px;font-weight:600;color:#1a6b3c;">
                    💊 Recommendation
                </p>
                <p style="margin:0;font-size:13px;color:#4a5568;line-height:1.6;">
                    {recommendation}
                </p>
            </div>
        """ if recommendation else ""

        msg = Message(
            subject=f"{emoji} DairyWatch Alert: {title}",
            recipients=[recipient],
            html=f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
                <div style="background:#1a6b3c;padding:24px;border-radius:12px 12px 0 0;">
                    <h1 style="color:white;margin:0;font-size:20px;">🐄 DairyWatch Alert</h1>
                </div>
                <div style="background:#f9f6f0;padding:24px;border-radius:0 0 12px 12px;
                            border:1px solid #e2e8e4;">
                    <h2 style="color:#1e2d24;font-size:16px;">{emoji} {title}</h2>
                    <div style="margin-bottom:4px;">
                        <p style="margin:0 0 6px 0;font-size:12px;font-weight:600;
                            color:#4a5568;">🩺 Diagnosis</p>
                        <p style="color:#4a5568;font-size:14px;line-height:1.6;margin:0;">
                            {message}
                        </p>
                    </div>
                    {recommendation_html}
                    <div style="margin-top:20px;padding:12px 16px;background:white;
                                border-radius:8px;border-left:4px solid #1a6b3c;">
                        <p style="margin:0;font-size:12px;color:#8a9ba8;">
                            Severity: <strong>{severity.upper()}</strong> ·
                            Sent by DairyWatch Monitoring System
                        </p>
                    </div>
                </div>
            </div>
            """
        )
        mail.send(msg)
    except Exception as e:
        print(f"Email failed: {e}")

# ─────────────────────────────────────────────────
#  DEBUG ROUTE
# ─────────────────────────────────────────────────

@app.route("/api/debug", methods=["GET"])
def debug():
    auth_header = request.headers.get("Authorization", "NOT PRESENT")
    cookie      = request.cookies.get("access_token", "NOT PRESENT")
    return jsonify({
        "auth_header": auth_header,
        "cookie":      cookie,
        "all_cookies": dict(request.cookies),
        "all_headers": dict(request.headers)
    }), 200


# ─────────────────────────────────────────────────
#  AUTH ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()

    if not data.get("name") or not data.get("email") or not data.get("password"):
        return jsonify({"error": "Name, email and password are required"}), 400

    if User.query.filter_by(email=data["email"]).first():
        return jsonify({"error": "Email already registered"}), 409

    user = User(
        name     = data["name"],
        email    = data["email"],
        password = generate_password_hash(data["password"]),
        role     = data.get("role", "worker")
    )
    db.session.add(user)
    db.session.commit()

    token = create_access_token(identity=str(user.id))

    from flask import make_response
    response = make_response(jsonify({
        "message": "Account created",
        "token":   token,
        "user":    {"id": user.id, "name": user.name, "role": user.role}
    }), 201)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,
        samesite="Lax",
        path="/",
        max_age=60 * 60 * 8
    )
    return response


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email")).first()

    if not user or not check_password_hash(user.password, data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_access_token(identity=str(user.id))

    from flask import make_response
    response = make_response(jsonify({
        "token": token,
        "user":  {"id": user.id, "name": user.name, "role": user.role}
    }), 200)
    response.set_cookie(
        "access_token",
        token,
        httponly=False,
        samesite="Lax",
        path="/",
        max_age=60 * 60 * 8
    )
    return response


@app.route("/api/auth/me", methods=["GET"])
@jwt_required()
def me():
    user = User.query.get(int(get_jwt_identity()))
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"id": user.id, "name": user.name, "role": user.role}), 200


# ─────────────────────────────────────────────────
#  COW ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/cows/stats", methods=["GET"])
@jwt_required()
def herd_stats():
    user_id  = int(get_jwt_identity())
    total    = Cow.query.filter_by(user_id=user_id).count()
    healthy  = Cow.query.filter_by(user_id=user_id, status="healthy").count()
    sick     = Cow.query.filter_by(user_id=user_id, status="sick").count()
    pregnant = Cow.query.filter_by(user_id=user_id, status="pregnant").count()
    return jsonify({
        "total":    total,
        "healthy":  healthy,
        "sick":     sick,
        "pregnant": pregnant
    }), 200


@app.route("/api/cows", methods=["GET"])
@jwt_required()
def get_cows():
    user_id = int(get_jwt_identity())
    cows    = Cow.query.filter_by(user_id=user_id).order_by(Cow.tag_number).all()
    return jsonify([cow.to_dict() for cow in cows]), 200

@app.route("/api/cows", methods=["POST"])
@jwt_required()
def add_cow():
    data    = request.get_json()
    user_id = int(get_jwt_identity())

    if not data.get("tag_number"):
        return jsonify({"error": "tag_number is required"}), 400

    # Check duplicate tag only within this user's herd
    if Cow.query.filter_by(tag_number=data["tag_number"], user_id=user_id).first():
        return jsonify({"error": "A cow with that tag number already exists"}), 409

    cow = Cow(
        user_id    = user_id,
        name       = data.get("name"),
        tag_number = data["tag_number"],
        breed      = data.get("breed"),
        status     = data.get("status", "healthy"),
        notes      = data.get("notes")
    )

    if data.get("date_of_birth"):
        from datetime import date
        cow.date_of_birth = date.fromisoformat(data["date_of_birth"])

    db.session.add(cow)
    db.session.commit()
    return jsonify(cow.to_dict()), 201


@app.route("/api/cows/<int:cow_id>", methods=["GET"])
@jwt_required()
def get_cow(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    return jsonify(cow.to_dict()), 200


@app.route("/api/cows/<int:cow_id>", methods=["PUT"])
@jwt_required()
def update_cow(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    data    = request.get_json()

    for field in ["name", "breed", "status", "notes"]:
        if field in data:
            setattr(cow, field, data[field])

    db.session.commit()
    return jsonify(cow.to_dict()), 200


@app.route("/api/cows/<int:cow_id>", methods=["DELETE"])
@jwt_required()
def delete_cow(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    db.session.delete(cow)
    db.session.commit()
    return jsonify({"message": f"Cow {cow.tag_number} deleted"}), 200
# ─────────────────────────────────────────────────
#  HEALTH ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/health/<int:cow_id>", methods=["GET"])
@jwt_required()
def get_health_records(cow_id):
    user_id = int(get_jwt_identity())
    Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    records = HealthRecord.query\
        .filter_by(cow_id=cow_id)\
        .order_by(HealthRecord.recorded_at.desc())\
        .limit(20).all()
    return jsonify([r.to_dict() for r in records]), 200


@app.route("/api/health/<int:cow_id>", methods=["POST"])
@jwt_required()
def log_health(cow_id):
    user_id   = int(get_jwt_identity())                                          # ← add this
    cow       = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()  # ← add this
    data      = request.get_json()
    user      = User.query.get(user_id)                                          # ← add this
    ai_result = analyze_cow_health(cow, data)

    record = HealthRecord(
        cow_id            = cow_id,
        temperature       = data.get("temperature"),
        is_limping        = data.get("is_limping", False),
        is_lethargic      = data.get("is_lethargic", False),
        is_not_eating     = data.get("is_not_eating", False),
        notes             = data.get("notes"),
        ai_diagnosis      = ai_result["diagnosis"],
        ai_recommendation = ai_result["recommendation"],
        flagged           = ai_result["flagged"]
    )
    db.session.add(record)

    if ai_result["severity"] == "critical":
        cow.status = "sick"

    if ai_result["flagged"]:
        alert_title   = f"Health concern — {cow.name or cow.tag_number}"
        alert_message = ai_result["diagnosis"]
        db.session.add(Alert(
            cow_id   = cow_id,
            type     = "health",
            severity = ai_result["severity"],
            title    = alert_title,
            message  = alert_message
        ))
        send_alert_email(
        alert_title,
        alert_message,
        ai_result["severity"],
        user.email,
        ai_result["recommendation"]
  )

    db.session.commit()
    return jsonify({
        "record":      record.to_dict(),
        "ai_analysis": ai_result
    }), 201


# ─────────────────────────────────────────────────
#  MILK ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/milk/summary", methods=["GET"])
@jwt_required()
def milk_summary():
    from sqlalchemy import func
    from datetime import timedelta

    user_id   = int(get_jwt_identity())
    today     = datetime.utcnow().date()
    week_ago  = datetime.utcnow() - timedelta(days=7)
    month_ago = datetime.utcnow() - timedelta(days=30)

    # Get only this user's cow ids
    cow_ids = [c.id for c in Cow.query.filter_by(user_id=user_id).all()]

    def total_since(since):
        result = db.session.query(func.sum(MilkRecord.yield_litres))\
            .filter(
                MilkRecord.cow_id.in_(cow_ids),
                MilkRecord.recorded_at >= since
            ).scalar()
        return round(result or 0, 2)

    return jsonify({
        "today":      total_since(datetime.combine(today, datetime.min.time())),
        "this_week":  total_since(week_ago),
        "this_month": total_since(month_ago)
    }), 200




@app.route("/api/milk/<int:cow_id>", methods=["GET"])
@jwt_required()
def get_milk_records(cow_id):
    user_id = int(get_jwt_identity())
    Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    records = MilkRecord.query\
        .filter_by(cow_id=cow_id)\
        .order_by(MilkRecord.recorded_at.desc())\
        .limit(30).all()
    return jsonify([r.to_dict() for r in records]), 200

@app.route("/api/milk/<int:cow_id>", methods=["POST"])
@jwt_required()
def log_milk(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    data    = request.get_json()
    user    = User.query.get(user_id)

    if not data.get("session") or not data.get("yield_litres"):
        return jsonify({"error": "session and yield_litres are required"}), 400

    record = MilkRecord(
        cow_id       = cow_id,
        session      = data["session"],
        yield_litres = data["yield_litres"]
    )
    db.session.add(record)

    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent   = MilkRecord.query.filter(
        MilkRecord.cow_id      == cow_id,
        MilkRecord.session     == data["session"],
        MilkRecord.recorded_at >= week_ago
    ).all()

    if recent:
        avg  = sum(r.yield_litres for r in recent) / len(recent)
        drop = ((avg - data["yield_litres"]) / avg) * 100

        if drop >= 20:
            alert_title   = f"Milk drop detected — {cow.name or cow.tag_number}"
            alert_message = f"Yield dropped {drop:.1f}% below 7-day average ({avg:.1f}L → {data['yield_litres']}L)"

            milk_recommendation = None
            try:
                prompt = f"""
A dairy cow named {cow.name or cow.tag_number} (Breed: {cow.breed or "Unknown"})
has shown a {drop:.1f}% drop in milk yield during the {data['session']} session.
Average was {avg:.1f}L, today it is {data['yield_litres']}L.
What are the most likely causes and what should the farmer do?
Respond in 2-3 sentences only, practical advice.
"""
                ai_response = ai_client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[
                        {"role": "system", "content": "You are a dairy cattle veterinary AI. Give concise practical advice."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=150
                )
                milk_recommendation = ai_response.choices[0].message.content.strip()
            except Exception as e:
                print(f"Milk AI recommendation error: {e}")
                milk_recommendation = "Check the cow for signs of mastitis, stress, or illness. Consider a full health check if the drop persists for more than 2 sessions."

            db.session.add(Alert(
                cow_id   = cow_id,
                type     = "production",
                severity = "warning",
                title    = alert_title,
                message  = alert_message
            ))
            send_alert_email(
                alert_title,
                alert_message,
                "warning",
                user.email,
                milk_recommendation
            )

    db.session.commit()
    return jsonify(record.to_dict()), 201

# ─────────────────────────────────────────────────
#  ALERT ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/alerts/count", methods=["GET"])
@jwt_required()
def alert_count():
    user_id  = int(get_jwt_identity())
    cow_ids  = [c.id for c in Cow.query.filter_by(user_id=user_id).all()]
    critical = Alert.query.filter(Alert.resolved == False, Alert.severity == "critical", Alert.cow_id.in_(cow_ids)).count()
    warning  = Alert.query.filter(Alert.resolved == False, Alert.severity == "warning", Alert.cow_id.in_(cow_ids)).count()
    return jsonify({"critical": critical, "warning": warning}), 200


@app.route("/api/alerts", methods=["GET"])
@jwt_required()
def get_alerts():
    user_id  = int(get_jwt_identity())
    cow_ids  = [c.id for c in Cow.query.filter_by(user_id=user_id).all()]
    resolved = request.args.get("resolved", "false").lower() == "true"
    alerts   = Alert.query\
        .filter(Alert.resolved == resolved, Alert.cow_id.in_(cow_ids))\
        .order_by(Alert.created_at.desc())\
        .limit(50).all()
    return jsonify([a.to_dict() for a in alerts]), 200

@app.route("/api/alerts/<int:alert_id>/resolve", methods=["PATCH"])
@jwt_required()
def resolve_alert(alert_id):
    alert          = Alert.query.get_or_404(alert_id)
    alert.resolved = True
    db.session.commit()
    return jsonify({"message": "Alert resolved", "alert": alert.to_dict()}), 200


# ─────────────────────────────────────────────────
#  IMAGE ANALYSIS ROUTE
# ─────────────────────────────────────────────────

@app.route("/api/analyze/image/<int:cow_id>", methods=["POST"])
@jwt_required()
def analyze_image(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    user    = User.query.get(user_id)
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]

    if not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({"error": "File must be an image (jpg, png, webp)"}), 400

    try:
        import base64, json
        image_data = base64.b64encode(file.read()).decode("utf-8")
        extension  = file.filename.rsplit(".", 1)[1].lower()
        media_type = "image/jpeg" if extension in ["jpg", "jpeg"] else f"image/{extension}"

        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": "You are a veterinary AI assistant specializing in dairy cattle."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
                    {"type": "text", "text": f"""Analyze this image of dairy cow {cow.name or cow.tag_number}.
Look for: body condition, coat quality, posture, injuries, eye clarity, alertness.
Respond ONLY in this JSON format:
{{
  "observations": "what you see",
  "concerns": "any health concerns or none",
  "recommendation": "what the farmer should do",
  "severity": "normal or warning or critical",
  "flagged": true or false
}}"""}
                ]}
            ],
            max_tokens=500
        )

        result = parse_ai_json(response)

        if result.get("flagged"):
            db.session.add(HealthRecord(
                cow_id            = cow_id,
                ai_diagnosis      = result.get("observations"),
                ai_recommendation = result.get("recommendation"),
                flagged           = True,
                notes             = "Flagged via image analysis"
            ))
            db.session.add(Alert(
                cow_id   = cow_id,
                type     = "health",
                severity = result.get("severity", "warning"),
                title    = f"Visual concern — {cow.name or cow.tag_number}",
                message  = result.get("concerns")
            ))
            

            send_alert_email(
           f"Visual concern — {cow.name or cow.tag_number}",
            result.get("concerns"),
            result.get("severity", "warning"),
            user.email,
            result.get("recommendation")
)
        db.session.commit()

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


# ─────────────────────────────────────────────────
#  VIDEO ANALYSIS ROUTE
# ─────────────────────────────────────────────────

@app.route("/api/analyze/video/<int:cow_id>", methods=["POST"])
@jwt_required()
def analyze_video(cow_id):
    user_id = int(get_jwt_identity())
    cow     = Cow.query.filter_by(id=cow_id, user_id=user_id).first_or_404()
    user    = User.query.get(user_id)

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]

    if not allowed_file(file.filename, ALLOWED_VIDEOS):
        return jsonify({"error": "File must be a video (mp4, mov, avi)"}), 400

    try:
        import cv2, base64, json, os as _os

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        cap         = cv2.VideoCapture(tmp_path)
        fps         = cap.get(cv2.CAP_PROP_FPS) or 25
        interval    = int(fps * 2)
        frames      = []
        frame_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_count % interval == 0:
                _, buffer = cv2.imencode(".jpg", frame)
                frames.append(base64.b64encode(buffer).decode("utf-8"))
                if len(frames) >= 5:
                    break
            frame_count += 1

        cap.release()
        _os.unlink(tmp_path)

        if not frames:
            return jsonify({"error": "Could not extract frames from video"}), 400

        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f}"}}
            for f in frames
        ]
        content.append({"type": "text", "text": f"""These are {len(frames)} frames from a video of cow {cow.name or cow.tag_number}.
Analyze for: gait, behavior, body condition, distress.
Respond ONLY in this JSON format:
{{
  "observations": "what you observed",
  "concerns": "any health concerns or none",
  "recommendation": "what the farmer should do",
  "severity": "normal or warning or critical",
  "flagged": true or false,
  "frames_analyzed": {len(frames)}
}}"""})

        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": "You are a veterinary AI. Analyze dairy cattle video frames."},
                {"role": "user",   "content": content}
            ],
            max_tokens=600
        )

        result = parse_ai_json(response)

        if result.get("flagged"):
            db.session.add(HealthRecord(
                cow_id            = cow_id,
                ai_diagnosis      = result.get("observations"),
                ai_recommendation = result.get("recommendation"),
                flagged           = True,
                notes             = f"Flagged via video — {len(frames)} frames"
            ))
            db.session.add(Alert(
                cow_id   = cow_id,
                type     = "health",
                severity = result.get("severity", "warning"),
                title    = f"Video concern — {cow.name or cow.tag_number}",
                message  = result.get("concerns")
            ))
            send_alert_email(
            f"Video concern — {cow.name or cow.tag_number}",
            result.get("concerns"),
            result.get("severity", "warning"),
            user.email,
            result.get("recommendation")
)
            db.session.commit()

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": f"Video analysis failed: {str(e)}"}), 500


# ─────────────────────────────────────────────────
#  AI CHAT ROUTE
# ─────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
@jwt_required()
def chat():
    data         = request.get_json()
    message      = data.get("message", "")
    history      = data.get("history", [])
    farm_context = data.get("farm_context", {})
    user_id      = int(get_jwt_identity())
    user         = User.query.get(user_id)
    
    
    recent_findings = db.session.query(HealthRecord, Cow)\
        .join(Cow, HealthRecord.cow_id == Cow.id)\
        .filter(
            
            HealthRecord.flagged == True,
            Cow.user_id == user_id
                
        )\
        .order_by(HealthRecord.recorded_at.desc())\
        .limit(5).all()
        
    findings_text = ""
    if recent_findings:
        lines = []
        for record, cow in recent_findings:
            source = "image/video upload" if not record.temperature else "health check"
            lines.append(
                f"- [{record.recorded_at.strftime('%Y-%m-%d %H:%M')}] "
                f"Cow {cow.name or cow.tag_number} ({source}): "
                f"Diagnosis: {record.ai_diagnosis}. "
                f"Recommendation: {record.ai_recommendation}."
            )
        findings_text = "\n".join(lines)
    else:
        findings_text = "No recent flagged findings."
                    
                    

    system_prompt = f"""
You are a helpful AI assistant for DairyWatch, a dairy farm monitoring system.
You are talking to a farmer named {farm_context.get('farmer_name', 'Farmer')}.

THEIR FARM DATA RIGHT NOW:
- Total cows: {farm_context.get('total_cows', 'unknown')}
- Healthy cows: {farm_context.get('healthy_cows', 'unknown')}
- Sick cows: {farm_context.get('sick_cows', 'unknown')}
- Pregnant cows: {farm_context.get('pregnant_cows', 'unknown')}
- Critical alerts: {farm_context.get('critical_alerts', 0)}
- Warning alerts: {farm_context.get('warning_alerts', 0)}
- Milk today: {farm_context.get('milk_today', 'unknown')} litres
- Milk this week: {farm_context.get('milk_this_week', 'unknown')} litres
- Milk this month: {farm_context.get('milk_this_month', 'unknown')} litres

RECENT FLAGGED FINDINGS FROM IMAGE/VIDEO ANALYSIS:
{findings_text}

YOUR ROLE:
- Answer questions about their farm using the data above
- Give practical dairy farming advice
- Explain veterinary terms simply
- Be warm, helpful and concise
- Use bullet points when listing multiple things
"""
    messages  = [{"role": "system", "content": system_prompt}]
    messages += history[-10:]
    messages.append({"role": "user", "content": message})

    try:
        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply}), 200

    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({
            "reply": "I'm having trouble connecting right now. Please try again shortly."
        }), 200


# ─────────────────────────────────────────────────
#  DASHBOARD ROUTES
# ─────────────────────────────────────────────────

@app.route("/api/dashboard/milk-chart", methods=["GET"])
@jwt_required()
def milk_chart_data():
    from sqlalchemy import func, cast, Date
    from datetime import timedelta

    user_id = int(get_jwt_identity())
    cow_ids = [c.id for c in Cow.query.filter_by(user_id=user_id).all()]

    labels, values = [], []
    for i in range(6, -1, -1):
        day   = datetime.utcnow().date() - timedelta(days=i)
        total = db.session.query(func.sum(MilkRecord.yield_litres))\
            .filter(
                MilkRecord.cow_id.in_(cow_ids),
                cast(MilkRecord.recorded_at, Date) == day
            ).scalar()
        labels.append(day.strftime("%a"))
        values.append(round(total or 0, 1))

    return jsonify({"labels": labels, "values": values}), 200

@app.route("/api/dashboard/insights", methods=["GET"])
@jwt_required()
def dashboard_insights():
    user_id = int(get_jwt_identity())
    cow_ids = [c.id for c in Cow.query.filter_by(user_id=user_id).all()]

    records = db.session.query(HealthRecord, Cow)\
        .join(Cow, HealthRecord.cow_id == Cow.id)\
        .filter(
            HealthRecord.flagged == True,
            Cow.user_id == user_id
        )\
        .order_by(HealthRecord.recorded_at.desc())\
        .limit(3).all()

    return jsonify([{
        "cow_name":     cow.name or cow.tag_number,
        "ai_diagnosis": record.ai_diagnosis,
        "recorded_at":  record.recorded_at.isoformat()
    } for record, cow in records]), 200



# ─────────────────────────────────────────────────
#  PAGE ROUTES
# ─────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("landing.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/cows")
def cows_page():
    return render_template("cows.html")

@app.route("/alerts")
def alerts_page():
    return render_template("alerts.html")

@app.route("/chat")
def chat_page():
    return render_template("chat.html")

@app.route("/camera")
def camera_page():
    return render_template("camera.html")

@app.route("/api/video-feeds", methods=["POST"])
@jwt_required()
def save_video_feed():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file   = request.files["file"]
    title  = request.form.get("title", "Farm Recording")
    cow_id = request.form.get("cow_id", None)
    if cow_id:
        cow_id = int(cow_id)

    if not allowed_file(file.filename, ALLOWED_VIDEOS):
        return jsonify({"error": "Invalid file type"}), 400

    try:
        import uuid, base64, json
        filename  = f"{user_id}_{uuid.uuid4().hex}.mp4"
        filepath  = os.path.join(VIDEO_FEEDS_DIR, filename)
        file.save(filepath)

        # ── Validate file was saved with content ──
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({"error": "File upload failed - no content received"}), 400

        print(f"Video file saved: {filename} ({os.path.getsize(filepath)} bytes)")

        # ── Extract first frame for analysis ────
        import cv2
        cap         = cv2.VideoCapture(filepath)
        
        ret, frame = cap.read()
        if not ret:
            cap.release()
            return jsonify({"error": "Could not extract frame from video - file may be corrupted or in unsupported format"}), 400
        
        _, buffer = cv2.imencode(".jpg", frame)
        frame_data = base64.b64encode(buffer).decode("utf-8")
        cap.release()

        # ── Build AI prompt with single frame ───
        cow_context = "general farm footage with multiple cows"
        if cow_id:
            cow = Cow.query.filter_by(id=cow_id, user_id=user_id).first()
            if cow:
                cow_context = f"cow named {cow.name or cow.tag_number} (Breed: {cow.breed or 'Unknown'})"

        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_data}"}}
        ]
        content.append({"type": "text", "text": f"""
This is a frame from a {title} recording of {cow_context}.

Analyze the frame carefully for:
- Animal behavior and activity levels
- Signs of distress, illness, or injury
- Feeding and movement patterns
- Any unusual events or concerns
- Overall herd/animal welfare

Respond ONLY in this exact JSON format:
{{
  "summary": "overall description of what you observed",
  "concerns": "specific health or welfare concerns, or 'none'",
  "recommendation": "what the farmer should do based on this footage",
  "severity": "normal or warning or critical",
  "flagged": true or false,
  "frame_analyzed": "first frame"
}}
"""})

        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": "You are a veterinary AI analyzing dairy farm footage. Always respond with valid JSON only."},
                {"role": "user",   "content": content}
            ],
            max_tokens=600
        )

        
        # strip markdown fences if present
        result = parse_ai_json(response)

        # ── Save to database ──────────────────
        feed = VideoFeed(
            user_id          = user_id,
            cow_id           = cow_id,
            title            = title,
            filename         = filename,
            ai_summary       = result.get("summary"),
            ai_concerns      = result.get("concerns"),
            ai_recommendation = result.get("recommendation"),
            severity         = result.get("severity", "normal"),
            flagged          = result.get("flagged", False)
        )
        db.session.add(feed)

        # ── Create alert if flagged ───────────
        if result.get("flagged"):
            db.session.add(Alert(
                cow_id   = cow_id,
                type     = "video",
                severity = result.get("severity", "warning"),
                title    = f"Video concern — {title}",
                message  = result.get("concerns")
            ))
            send_alert_email(
                f"Video concern — {title}",
                result.get("concerns"),
                result.get("severity", "warning"),
                user.email,
                result.get("recommendation")
            )

        db.session.commit()
        return jsonify({
            "feed":   feed.to_dict(),
            "result": result
        }), 201

    except Exception as e:
        import traceback
        print(f"Video feed error: {e}")
        traceback.print_exc()
        # Clean up partial file if it exists
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


@app.route("/api/video-feeds", methods=["GET"])
@jwt_required()
def get_video_feeds():
    user_id = int(get_jwt_identity())
    feeds   = VideoFeed.query\
        .filter_by(user_id=user_id)\
        .order_by(VideoFeed.recorded_at.desc())\
        .limit(20).all()
    return jsonify([f.to_dict() for f in feeds]), 200


@app.route("/api/video-feeds/<int:feed_id>", methods=["DELETE"])
@jwt_required()
def delete_video_feed(feed_id):
    user_id = int(get_jwt_identity())
    feed    = VideoFeed.query.filter_by(id=feed_id, user_id=user_id).first_or_404()

    # Delete file from disk
    filepath = os.path.join(VIDEO_FEEDS_DIR, feed.filename)
    if os.path.exists(filepath):
        os.remove(filepath)

    db.session.delete(feed)
    db.session.commit()
    return jsonify({"message": "Video feed deleted"}), 200

@app.route("/static/video_feeds/<path:filename>")
@jwt_required()
def serve_video(filename):
    return send_from_directory(VIDEO_FEEDS_DIR, filename)

@app.route("/api/analyze/general-image", methods=["POST"])
@jwt_required()
def analyze_general_image():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({"error": "File must be an image"}), 400

    try:
        import base64, json
        image_data = base64.b64encode(file.read()).decode("utf-8")
        extension  = file.filename.rsplit(".", 1)[1].lower()
        media_type = "image/jpeg" if extension in ["jpg", "jpeg"] else f"image/{extension}"

        response = ai_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": "You are a veterinary AI analyzing dairy farm images."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
                    {"type": "text", "text": """Analyze this dairy farm image.
Look for: animal health, behavior, body condition, environment, any concerns.
Respond ONLY in this JSON format:
{
  "observations": "what you see",
  "concerns": "any health or welfare concerns or none",
  "recommendation": "what the farmer should do",
  "severity": "normal or warning or critical",
  "flagged": true or false
}"""}
                ]}
            ],
            max_tokens=500
        )

        
        result = parse_ai_json(response)
        result["summary"] = result.get("observations")
        
        
        import uuid
        feed = VideoFeed(
            user_id          = user_id,
            cow_id           = None,
            title            = f"General Image Analysis - {file.filename}",
            filename         = None,
            ai_summary       = result.get("observations"),
            ai_concerns      = result.get("concerns"),
            ai_recommendation = result.get("recommendation"),
            severity         = result.get("severity", "normal"),
            flagged          = result.get("flagged", False)
        )
        db.session.add(feed)
        db.session.commit()

        if result.get("flagged"):
            first_cow = Cow.query.filter_by(user_id=user_id).first()
            if first_cow:
                db.session.add(HealthRecord(
                    cow_id            = first_cow.id,
                    ai_diagnosis      = result.get("observations"),
                    ai_recommendation = result.get("recommendation"),
                    flagged           = True,
                    notes             = "Flagged via general image analysis"
                ))
            db.session.commit()
            
        send_alert_email(
            "Visual concern detected from uploaded image",
            result.get("concerns"),
            result.get("severity", "warning"),
            user.email,
            result.get("recommendation")
        )

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500




# ─────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)