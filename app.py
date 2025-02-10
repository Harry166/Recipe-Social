from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from PIL import Image
import io
import cloudinary
import cloudinary.uploader
import cloudinary.api
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# Near the top of your file, after imports
basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'letsgo123'

# Database configuration
if os.environ.get('RENDER'):
    # For Render.com deployment - use PostgreSQL
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL').replace('postgres://', 'postgresql://')
else:
    # For local development - use SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "recipes.db")}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365 * 10)
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=365 * 10)

# Update your upload folder configuration
if os.environ.get('RENDER'):
    # For Render.com deployment
    UPLOAD_FOLDER = '/tmp/uploads'
    PROFILE_UPLOAD_FOLDER = '/tmp/uploads/profiles'
else:
    # For local development
    UPLOAD_FOLDER = 'static/uploads'
    PROFILE_UPLOAD_FOLDER = 'static/uploads/profiles'

# Create directories if they don't exist
if os.environ.get('RENDER'):
    os.makedirs('/tmp/uploads', exist_ok=True)
    os.makedirs('/tmp/uploads/profiles', exist_ok=True)
else:
    os.makedirs('static/uploads', exist_ok=True)
    os.makedirs('static/uploads/profiles', exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Define likes table first
likes = db.Table('likes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('recipe_id', db.Integer, db.ForeignKey('recipe.id'), primary_key=True)
)

# Add after likes table
followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    profile_pic = db.Column(db.String(100), default='default_profile.jpg')
    bio = db.Column(db.Text, default='')
    recipes = db.relationship('Recipe', backref='author', lazy=True)
    liked_recipes = db.relationship('Recipe', secondary=likes, 
                                  backref=db.backref('liked_by', lazy='dynamic'))
    followers = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('following', lazy='dynamic'),
        lazy='dynamic'
    )

    def save_profile_pic(self, picture_data):
        try:
            # Upload directly to Cloudinary
            result = cloudinary.uploader.upload(picture_data)
            return result['secure_url']
        except Exception as e:
            print(f"Cloudinary upload error: {str(e)}")
            return 'default.jpg'

    def like_recipe(self, recipe):
        if not self.has_liked_recipe(recipe):
            self.liked_recipes.append(recipe)

    def unlike_recipe(self, recipe):
        if self.has_liked_recipe(recipe):
            self.liked_recipes.remove(recipe)

    def has_liked_recipe(self, recipe):
        return recipe in self.liked_recipes

    def follow(self, user):
        if not self.is_following(user):
            self.following.append(user)

    def unfollow(self, user):
        if self.is_following(user):
            self.following.remove(user)

    def is_following(self, user):
        return self.following.filter(followers.c.followed_id == user.id).count() > 0

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

# Add these mood mappings
MOOD_MAPPINGS = {
    "Happy": ["fresh", "bright", "colorful", "light", "energizing", "sweet", "fruity"],
    "Sad": ["comfort", "warm", "creamy", "chocolate", "hearty", "rich"],
    "Energetic": ["protein", "healthy", "fresh", "smoothie", "light", "citrus"],
    "Tired": ["easy", "quick", "simple", "comforting", "warm"],
    "Stressed": ["soothing", "simple", "comfort", "warm", "familiar"],
    "Adventurous": ["spicy", "exotic", "unique", "international", "complex"]
}

def get_recipe_recommendations(mood, recipes, num_recommendations=5):
    # If no recipes or no mood, return all recipes
    if not recipes or not mood:
        return recipes
        
    try:
        # Create a string representation of each recipe
        recipe_texts = []
        for recipe in recipes:
            text = f"{recipe.title} {recipe.ingredients} {recipe.instructions}"
            recipe_texts.append(text.lower())
        
        # If no recipe texts, return all recipes
        if not recipe_texts:
            return recipes
            
        # Create mood text from mapping
        mood_keywords = " ".join(MOOD_MAPPINGS.get(mood, []))
        if not mood_keywords:  # If mood not found in mappings
            return recipes
            
        # Add mood text to the documents
        all_texts = recipe_texts + [mood_keywords]
        
        # Create TF-IDF vectors
        vectorizer = TfidfVectorizer(stop_words='english')
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        
        # Calculate similarity between mood and recipes
        mood_vector = tfidf_matrix[-1]
        recipe_vectors = tfidf_matrix[:-1]
        similarities = cosine_similarity(recipe_vectors, mood_vector)
        
        # Get top recommendations
        top_indices = similarities.flatten().argsort()[-num_recommendations:][::-1]
        recommended = [recipes[i] for i in top_indices]
        
        # If no recommendations found, return all recipes
        return recommended if recommended else recipes
        
    except Exception as e:
        print(f"Error in recommendations: {str(e)}")
        return recipes  # Return all recipes on error

@app.route('/')
def home():
    mood = request.args.get('mood')
    top_recipes = Recipe.query.order_by(Recipe.views.desc()).limit(3).all()
    
    if mood:
        all_recipes = Recipe.query.all()
        filtered_recipes = get_recipe_recommendations(mood, all_recipes)
    else:
        filtered_recipes = Recipe.query.order_by(Recipe.date_posted.desc()).all()
    
    return render_template('home.html', 
                         top_recipes=top_recipes, 
                         all_recipes=filtered_recipes)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose a different one.', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please use a different email.', 'error')
            return render_template('register.html')
        
        user = User(username=username, 
                   email=email, 
                   password=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user, remember=True)
        return redirect(url_for('setup_profile'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('home'))
        else:
            flash('Invalid username or password', 'danger')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

@app.route('/recipe/new', methods=['GET', 'POST'])
@login_required
def new_recipe():
    if request.method == 'POST':
        try:
            file = request.files['image']
            image_url = 'default.jpg'
            
            if file and file.filename != '':
                # Upload directly to Cloudinary
                result = cloudinary.uploader.upload(file)
                image_url = result['secure_url']
            
            recipe = Recipe(
                title=request.form['title'],
                ingredients=request.form['ingredients'],
                preparation_time=request.form['preparation_time'],
                instructions=request.form['instructions'],
                image_file=image_url,
                author=current_user
            )
            db.session.add(recipe)
            db.session.commit()
            return redirect(url_for('home'))
        except Exception as e:
            print(f"Recipe creation error: {str(e)}")
            flash('Error creating recipe', 'danger')
            return render_template('create_recipe.html')
            
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

@app.route('/like/<int:recipe_id>', methods=['POST'])
@login_required
def like_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    current_user.like_recipe(recipe)
    db.session.commit()
    return jsonify({'likes': len(recipe.liked_by.all())})

@app.route('/unlike/<int:recipe_id>', methods=['POST'])
@login_required
def unlike_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    current_user.unlike_recipe(recipe)
    db.session.commit()
    return jsonify({'likes': len(recipe.liked_by.all())})

@app.route('/profile/<username>')
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    # Get user's recipes
    recipes = Recipe.query.filter_by(author=user).order_by(Recipe.date_posted.desc()).all()
    return render_template('profile.html', user=user, recipes=recipes)

@app.route('/setup_profile', methods=['GET', 'POST'])
@login_required
def setup_profile():
    if request.method == 'POST':
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename != '':
                try:
                    # Upload directly to Cloudinary
                    result = cloudinary.uploader.upload(file)
                    current_user.profile_pic = result['secure_url']
                    db.session.commit()
                except Exception as e:
                    print(f"Upload error: {str(e)}")
                    flash('Error uploading file', 'danger')
                    return render_template('setup_profile.html')

        if 'bio' in request.form:
            current_user.bio = request.form['bio']
            db.session.commit()
        
        return redirect(url_for('profile', username=current_user.username))
    
    return render_template('setup_profile.html')

@app.route('/follow/<username>', methods=['POST'])
@login_required
def follow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user != current_user and not current_user.is_following(user):
        current_user.follow(user)
        db.session.commit()
    return jsonify({
        'followers_count': user.followers.count(),
        'is_following': True
    })

@app.route('/unfollow/<username>', methods=['POST'])
@login_required
def unfollow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user != current_user and current_user.is_following(user):
        current_user.unfollow(user)
        db.session.commit()
    return jsonify({
        'followers_count': user.followers.count(),
        'is_following': False
    })

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/db-status')
def db_status():
    try:
        # Try to query the database
        User.query.first()
        return jsonify({'status': 'Database is working'})
    except Exception as e:
        return jsonify({'status': 'Database error', 'error': str(e)}), 500

@app.route('/refresh-all')
def refresh_all():
    try:
        with app.app_context():
            # Drop all tables
            db.drop_all()
            # Recreate all tables
            db.create_all()
            return "Database completely refreshed!"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/test-upload')
def test_upload():
    try:
        # Try a test upload with a simple string
        result = cloudinary.uploader.upload("https://res.cloudinary.com/demo/image/upload/v1312461204/sample.jpg")
        return jsonify({
            'status': 'success',
            'url': result['secure_url']
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500

@app.route('/setup-official-content')
def setup_official_content():
    try:
        official = User.query.filter_by(username="Recipe Social").first()
        if official:
            # Update existing recipes' preparation times
            recipes = Recipe.query.filter_by(user_id=official.id).all()
            for recipe in recipes:
                # Convert times like "1 hour 30 minutes" to "90 minutes"
                current_time = recipe.preparation_time
                if 'hour' in current_time.lower():
                    # Parse the time and convert to minutes
                    parts = current_time.lower().replace('minutes', '').replace('minute', '').replace('hours', 'hour').split('hour')
                    hours = int(parts[0].strip())
                    minutes = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 0
                    total_minutes = hours * 60 + minutes
                    recipe.preparation_time = f"{total_minutes} minutes"
                elif not current_time.endswith('minutes'):
                    # Add "minutes" if it's missing
                    recipe.preparation_time = f"{current_time.strip()} minutes"
            
            db.session.commit()
            return "Recipe times updated successfully!"
            
        return "Official account not found"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/create-test-recipe')
def create_test_recipe():
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload("https://res.cloudinary.com/demo/image/upload/v1312461204/sample.jpg")
        
        # Create test recipe
        recipe = Recipe(
            title="Test Recipe",
            ingredients="Test ingredients",
            preparation_time="5 minutes",
            instructions="Test instructions",
            image_file=result['secure_url'],
            user_id=1
        )
        db.session.add(recipe)
        db.session.commit()
        return "Test recipe created with real image!"
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/get_mood_recipes', methods=['POST'])
def get_mood_recipes():
    try:
        mood = request.form.get('mood')
        all_recipes = Recipe.query.all()
        
        recommended_recipes = get_recipe_recommendations(mood, all_recipes)
        
        # Add mood to each recipe JSON object
        recipes_json = [{
            'id': recipe.id,
            'title': recipe.title,
            'image_file': recipe.image_file,
            'preparation_time': recipe.preparation_time,
            'views': recipe.views,
            'has_been_top': recipe.has_been_top,
            'date_posted': recipe.date_posted.strftime('%B %d, %Y'),
            'likes': len(recipe.liked_by.all()),
            'mood': mood  # Add the mood here
        } for recipe in recommended_recipes]
        
        return jsonify(recipes_json)
    except Exception as e:
        print(f"Error in get_mood_recipes: {str(e)}")
        return jsonify([])

@app.route("/sources")
def sources():
    return render_template('sources.html')

@app.route("/create_recipe", methods=['GET', 'POST'])
@login_required
def create_recipe():
    try:
        if request.method == 'POST':
            title = request.form.get('title')
            ingredients = '\n'.join(request.form.getlist('ingredients[]'))
            instructions = request.form.get('instructions')
            # Get the time value and ensure it includes "minutes"
            time_value = request.form.get('preparation_time')
            preparation_time = f"{time_value} minutes" if not time_value.endswith('minutes') else time_value
            
            # Handle image upload
            image_file = request.files.get('image')
            if image_file:
                image_filename = save_picture(image_file)
            else:
                image_filename = 'default.jpg'
                
            recipe = Recipe(
                title=title,
                ingredients=ingredients,
                instructions=instructions,
                preparation_time=preparation_time,
                author=current_user,
                image_file=image_filename
            )
            
            db.session.add(recipe)
            db.session.commit()
            flash('Your recipe has been created!', 'success')
            return redirect(url_for('home'))
            
        return render_template('create_recipe.html', title='New Recipe')
    except Exception as e:
        print(f"Error in create_recipe: {str(e)}")
        db.session.rollback()
        flash('An error occurred while creating the recipe.', 'danger')
        return redirect(url_for('home'))

def save_picture(picture_file):
    try:
        # Upload to Cloudinary
        result = cloudinary.uploader.upload(picture_file)
        return result['secure_url']
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return 'default.jpg'

# Configure Cloudinary
cloudinary.config( 
    cloud_name = "dxxxzdjmv",
    api_key = "992923442213286", 
    api_secret = "iMGp_riYsXFsfcaldx3bZdCHC4g"
)

if __name__ == '__main__' or os.environ.get('RENDER'):  # Add the RENDER check
    with app.app_context():
        try:
            db.create_all()
            print("Database tables created successfully!")
        except Exception as e:
            print(f"Error creating database tables: {str(e)}")
    
    if __name__ == '__main__':
        app.run(debug=True)
