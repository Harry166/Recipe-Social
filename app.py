from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///recipes.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    recipes = db.relationship('Recipe', backref='author', lazy=True)

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    ingredients = db.Column(db.Text, nullable=False)
    preparation_time = db.Column(db.String(50), nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    image_file = db.Column(db.String(100), nullable=False, default='default.jpg')
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    views = db.Column(db.Integer, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    has_been_top = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    top_recipes = Recipe.query.order_by(Recipe.views.desc()).limit(3).all()
    # Mark current top recipes
    for recipe in top_recipes:
        if not recipe.has_been_top:
            recipe.has_been_top = True
            db.session.add(recipe)
    db.session.commit()
    
    all_recipes = Recipe.query.order_by(Recipe.date_posted.desc()).all()
    return render_template('home.html', top_recipes=top_recipes, all_recipes=all_recipes)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        user = User(username=username, 
                   email=email, 
                   password=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('home'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/recipe/new', methods=['GET', 'POST'])
@login_required
def new_recipe():
    if request.method == 'POST':
        file = request.files['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        else:
            filename = 'default.jpg'
            
        # Get ingredients list and format it
        ingredients = request.form.getlist('ingredients[]')
        formatted_ingredients = '\n'.join(f'• {ingredient.strip()}' for ingredient in ingredients if ingredient.strip())
            
        recipe = Recipe(
            title=request.form['title'],
            ingredients=formatted_ingredients,
            preparation_time=request.form['preparation_time'],
            instructions=request.form['instructions'],
            image_file=filename,
            author=current_user
        )
        db.session.add(recipe)
        db.session.commit()
        return redirect(url_for('home'))
    return render_template('create_recipe.html')

@app.route('/recipe/<int:recipe_id>')
def recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    recipe.views += 1
    db.session.commit()
    return render_template('recipe.html', recipe=recipe)

@app.route('/search')
def search():
    query = request.args.get('q', '')
    if query:
        # Using SQL's LIKE operator for case-insensitive search
        search_results = Recipe.query.filter(
            Recipe.title.ilike(f'%{query}%')
        ).order_by(Recipe.date_posted.desc()).all()
    else:
        search_results = []
    
    return render_template('search.html', recipes=search_results, query=query)

if __name__ == '__main__':
    if not os.path.exists('static/uploads'):
        os.makedirs('static/uploads')
    with app.app_context():
        db.create_all()
    app.run(debug=True)
