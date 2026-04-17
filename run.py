from app import app, db
from flask_migrate import upgrade

if __name__ == "__main__":
    with app.app_context():
        upgrade()
    app.run(debug=True, host="0.0.0.0", port=5000)