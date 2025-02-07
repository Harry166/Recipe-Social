from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from PIL import Image
import io

app = Flask(__name__)
app.config['SECRET_KEY'] = 'letsgo123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///recipes.db'
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
os.makedirs('static', exist_ok=True)
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
        # Resize and save profile picture
        img = Image.open(picture_data)
        if img.height > 500 or img.width > 500:
            output_size = (500, 500)
            img.thumbnail(output_size)
        
        # Save the image
        filename = secure_filename(f'profile_{self.username}.jpg')
        img_path = os.path.join(PROFILE_UPLOAD_FOLDER, filename)
        img.save(img_path)
        return filename

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

@app.route('/')
def home():
    # Query recipes and order by number of likes
    top_recipes = Recipe.query.join(likes).group_by(Recipe.id)\
        .order_by(db.func.count(likes.c.user_id).desc())\
        .limit(3).all()
    
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
            # Handle image upload
            file = request.files['image']
            filename = 'default.jpg'
            
            if file and file.filename != '':
                if allowed_file(file.filename):
                    # Create upload directory if it doesn't exist
                    os.makedirs('static/uploads', exist_ok=True)
                    
                    filename = secure_filename(file.filename)
                    filepath = os.path.join('static/uploads', filename)
                    file.save(filepath)
                else:
                    flash('Invalid file type. Please use jpg, jpeg, png, or gif.', 'danger')
                    return render_template('create_recipe.html')
            
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
            flash('Recipe created successfully!', 'success')
            return redirect(url_for('home'))
            
        except Exception as e:
            print(f"Recipe creation error: {str(e)}")  # For debugging
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
                    # Create directories if they don't exist
                    os.makedirs('static/uploads/profiles', exist_ok=True)
                    
                    filename = secure_filename(file.filename)
                    filepath = os.path.join('static/uploads/profiles', filename)
                    file.save(filepath)
                    current_user.profile_pic = filename
                    db.session.commit()
                except Exception as e:
                    print(f"Upload error: {str(e)}")  # For debugging
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
