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
        official = User(
            username="Recipe Social",
            email="official@recipesocial.com",
            password=generate_password_hash("securepassword123"),
            bio="The official Recipe Social account.",
            profile_pic="https://images.unsplash.com/photo-1556911261-6bd341186b2f"
        )
        db.session.add(official)
        db.session.commit()

        recipes = [
            {
                "title": "Classic Homemade Pizza",
                "ingredients": "• 2 1/4 cups bread flour\n• 1 1/2 tsp instant yeast\n• 1 1/2 tsp salt\n• 1 cup warm water\n• 2 tbsp olive oil\n• Pizza sauce\n• Mozzarella cheese",
                "preparation_time": "1 hour",
                "instructions": "1. Mix flour, yeast, salt\n2. Add water and oil, knead\n3. Let rise 30 minutes\n4. Roll out, add toppings\n5. Bake at 450°F for 15 minutes",
                "image_file": "https://images.unsplash.com/photo-1513104890138-7c749659a591"
            },
            {
                "title": "Easy Chocolate Chip Cookies",
                "ingredients": "• 2 1/4 cups flour\n• 1 cup butter\n• 3/4 cup sugar\n• 3/4 cup brown sugar\n• 2 eggs\n• 2 cups chocolate chips",
                "preparation_time": "25 minutes",
                "instructions": "1. Cream butter and sugars\n2. Add eggs and vanilla\n3. Mix in dry ingredients\n4. Add chocolate chips\n5. Bake at 375°F for 10 minutes",
                "image_file": "https://images.unsplash.com/photo-1499636136210-6f4ee915583e"
            },
            {
                "title": "Creamy Mac and Cheese",
                "ingredients": "• 1 pound macaroni\n• 3 cups cheddar cheese\n• 2 cups milk\n• 1/4 cup butter\n• 1/4 cup flour\n• Salt and pepper",
                "preparation_time": "30 minutes",
                "instructions": "1. Cook pasta\n2. Make cheese sauce\n3. Combine and bake\n4. Enjoy hot",
                "image_file": "https://images.unsplash.com/photo-1543339494-b4cd4f7ba686"
            },
            {
                "title": "Fresh Garden Salad",
                "ingredients": "• Mixed greens\n• Cherry tomatoes\n• Cucumber\n• Red onion\n• Olive oil\n• Balsamic vinegar",
                "preparation_time": "10 minutes",
                "instructions": "1. Wash vegetables\n2. Chop ingredients\n3. Mix in bowl\n4. Add dressing",
                "image_file": "https://images.unsplash.com/photo-1512621776951-a57141f2eefd"
            },
            {
                "title": "Homemade Burger",
                "ingredients": "• Ground beef\n• Burger buns\n• Lettuce\n• Tomato\n• Onion\n• Cheese\n• Condiments",
                "preparation_time": "20 minutes",
                "instructions": "1. Form patties\n2. Grill to preference\n3. Toast buns\n4. Assemble burger",
                "image_file": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd"
            },
            {
                "title": "Breakfast Smoothie Bowl",
                "ingredients": "• Frozen berries\n• Banana\n• Greek yogurt\n• Honey\n• Granola\n• Chia seeds",
                "preparation_time": "10 minutes",
                "instructions": "1. Blend fruits and yogurt\n2. Pour in bowl\n3. Top with granola and seeds\n4. Drizzle honey",
                "image_file": "https://images.unsplash.com/photo-1511690743698-d9d85f2fbf38"
            },
            {
                "title": "Grilled Salmon",
                "ingredients": "• Salmon fillets\n• Lemon\n• Olive oil\n• Garlic\n• Herbs\n• Salt and pepper",
                "preparation_time": "20 minutes",
                "instructions": "1. Marinate salmon\n2. Heat grill\n3. Cook 4-5 mins each side\n4. Serve with lemon",
                "image_file": "https://images.unsplash.com/photo-1485921325833-c519f76c4927"
            },
            {
                "title": "Avocado Toast",
                "ingredients": "• Sourdough bread\n• Ripe avocado\n• Cherry tomatoes\n• Red pepper flakes\n• Salt and pepper",
                "preparation_time": "10 minutes",
                "instructions": "1. Toast bread\n2. Mash avocado\n3. Add toppings\n4. Season to taste",
                "image_file": "https://images.unsplash.com/photo-1541519227354-08fa5d50c44d"
            },
            {
                "title": "Chicken Stir Fry",
                "ingredients": "• Chicken breast\n• Mixed vegetables\n• Soy sauce\n• Ginger\n• Garlic\n• Rice",
                "preparation_time": "25 minutes",
                "instructions": "1. Cook rice\n2. Stir fry chicken\n3. Add vegetables\n4. Mix sauce and serve",
                "image_file": "https://images.unsplash.com/photo-1603133872878-684f208fb84b"
            },
            {
                "title": "Greek Salad",
                "ingredients": "• Cucumber\n• Tomatoes\n• Red onion\n• Feta cheese\n• Olives\n• Olive oil",
                "preparation_time": "15 minutes",
                "instructions": "1. Chop vegetables\n2. Add cheese and olives\n3. Dress with oil\n4. Season to taste",
                "image_file": "https://images.unsplash.com/photo-1540189549336-e6e99c3679fe"
            },
            {
                "title": "Homemade Sushi Roll",
                "ingredients": "• Sushi rice\n• Nori sheets\n• Cucumber\n• Avocado\n• Salmon/Tuna\n• Soy sauce",
                "preparation_time": "45 minutes",
                "instructions": "1. Cook sushi rice\n2. Prepare fillings\n3. Roll with nori\n4. Slice and serve",
                "image_file": "https://images.unsplash.com/photo-1579871494447-9811cf80d66c"
            },
            {
                "title": "Chocolate Banana Smoothie",
                "ingredients": "• Banana\n• Cocoa powder\n• Milk\n• Honey\n• Ice cubes\n• Vanilla extract",
                "preparation_time": "5 minutes",
                "instructions": "1. Add all ingredients\n2. Blend until smooth\n3. Pour and enjoy",
                "image_file": "https://images.unsplash.com/photo-1577805947697-89e18249d767"
            },
            {
                "title": "Vegetable Pasta",
                "ingredients": "• Penne pasta\n• Mixed vegetables\n• Olive oil\n• Garlic\n• Parmesan\n• Basil",
                "preparation_time": "20 minutes",
                "instructions": "1. Cook pasta\n2. Sauté vegetables\n3. Combine with sauce\n4. Top with cheese",
                "image_file": "https://images.unsplash.com/photo-1621996346565-e3dbc646d9a9"
            },
            {
                "title": "Berry Pancakes",
                "ingredients": "• Flour\n• Milk\n• Eggs\n• Mixed berries\n• Maple syrup\n• Butter",
                "preparation_time": "25 minutes",
                "instructions": "1. Mix batter\n2. Cook pancakes\n3. Add berries\n4. Serve with syrup",
                "image_file": "https://images.unsplash.com/photo-1528207776546-365bb710ee93"
            },
            {
                "title": "Chicken Caesar Salad",
                "ingredients": "• Romaine lettuce\n• Grilled chicken\n• Croutons\n• Parmesan\n• Caesar dressing",
                "preparation_time": "20 minutes",
                "instructions": "1. Grill chicken\n2. Chop lettuce\n3. Combine ingredients\n4. Add dressing",
                "image_file": "https://images.unsplash.com/photo-1550304943-4f24f54ddde9"
            },
            {
                "title": "Beef Tacos",
                "ingredients": "• Ground beef\n• Taco shells\n• Lettuce\n• Tomatoes\n• Cheese\n• Sour cream",
                "preparation_time": "25 minutes",
                "instructions": "1. Cook beef with spices\n2. Warm shells\n3. Prepare toppings\n4. Assemble tacos",
                "image_file": "https://images.unsplash.com/photo-1551504734-5ee1c4a1479b"
            },
            {
                "title": "Fruit Parfait",
                "ingredients": "• Greek yogurt\n• Granola\n• Mixed berries\n• Honey\n• Mint leaves",
                "preparation_time": "10 minutes",
                "instructions": "1. Layer yogurt\n2. Add fruits\n3. Top with granola\n4. Drizzle honey",
                "image_file": "https://images.unsplash.com/photo-1488477181946-6428a0291777"
            },
            {
                "title": "Mushroom Risotto",
                "ingredients": "• Arborio rice\n• Mushrooms\n• Onion\n• White wine\n• Parmesan\n• Butter",
                "preparation_time": "40 minutes",
                "instructions": "1. Sauté mushrooms\n2. Cook rice slowly\n3. Add stock gradually\n4. Finish with cheese",
                "image_file": "https://images.unsplash.com/photo-1476124369491-e7addf5db371"
            },
            {
                "title": "Caprese Sandwich",
                "ingredients": "• Ciabatta bread\n• Mozzarella\n• Tomatoes\n• Basil\n• Balsamic glaze",
                "preparation_time": "10 minutes",
                "instructions": "1. Slice bread\n2. Layer ingredients\n3. Add basil\n4. Drizzle balsamic",
                "image_file": "https://images.unsplash.com/photo-1528735602780-2552fd46c7af"
            },
            {
                "title": "Shrimp Scampi",
                "ingredients": "• Shrimp\n• Linguine\n• Garlic\n• White wine\n• Lemon\n• Parsley",
                "preparation_time": "25 minutes",
                "instructions": "1. Cook pasta\n2. Sauté shrimp\n3. Make sauce\n4. Combine all",
                "image_file": "https://images.unsplash.com/photo-1565557623262-b51c2513a641"
            },
            {
                "title": "Vegetable Curry",
                "ingredients": "• Mixed vegetables\n• Coconut milk\n• Curry paste\n• Rice\n• Cilantro\n• Lime",
                "preparation_time": "35 minutes",
                "instructions": "1. Cook rice\n2. Prepare curry sauce\n3. Add vegetables\n4. Simmer until done",
                "image_file": "https://images.unsplash.com/photo-1455619452474-d2be8b1e70cd"
            },
            {
                "title": "Banana Bread",
                "ingredients": "• Ripe bananas\n• Flour\n• Sugar\n• Eggs\n• Butter\n• Vanilla",
                "preparation_time": "1 hour",
                "instructions": "1. Mash bananas\n2. Mix ingredients\n3. Pour in pan\n4. Bake at 350°F",
                "image_file": "https://images.unsplash.com/photo-1605286658031-68f5f1d3681e"
            },
            {
                "title": "Quinoa Bowl",
                "ingredients": "• Quinoa\n• Roasted vegetables\n• Chickpeas\n• Avocado\n• Tahini dressing",
                "preparation_time": "30 minutes",
                "instructions": "1. Cook quinoa\n2. Roast vegetables\n3. Prepare dressing\n4. Assemble bowl",
                "image_file": "https://images.unsplash.com/photo-1543362906-acfc16c67564"
            },
            {
                "title": "Fish Tacos",
                "ingredients": "• White fish\n• Cabbage slaw\n• Lime crema\n• Tortillas\n• Avocado",
                "preparation_time": "25 minutes",
                "instructions": "1. Cook fish\n2. Make slaw\n3. Prepare crema\n4. Assemble tacos",
                "image_file": "https://images.unsplash.com/photo-1512838243191-e81e8f66f1fd"
            },
            {
                "title": "Chia Pudding",
                "ingredients": "• Chia seeds\n• Almond milk\n• Honey\n• Fresh fruits\n• Nuts",
                "preparation_time": "5 minutes + overnight",
                "instructions": "1. Mix ingredients\n2. Refrigerate overnight\n3. Add toppings\n4. Serve chilled",
                "image_file": "https://images.unsplash.com/photo-1458644267420-66bc8a5f21e4"
            },
            {
                "title": "Pesto Pasta",
                "ingredients": "• Spaghetti\n• Fresh basil\n• Pine nuts\n• Garlic\n• Parmesan\n• Olive oil",
                "preparation_time": "20 minutes",
                "instructions": "1. Cook pasta\n2. Blend pesto ingredients\n3. Combine\n4. Top with cheese",
                "image_file": "https://images.unsplash.com/photo-1473093226795-af9932fe5856"
            },
            {
                "title": "Overnight Oats",
                "ingredients": "• Rolled oats\n• Milk\n• Yogurt\n• Chia seeds\n• Honey\n• Berries",
                "preparation_time": "5 minutes + overnight",
                "instructions": "1. Mix ingredients\n2. Refrigerate overnight\n3. Add toppings\n4. Enjoy cold",
                "image_file": "https://images.unsplash.com/photo-1517673132405-a56a62b18caf"
            },
            {
                "title": "Beef Stir Fry",
                "ingredients": "• Beef strips\n• Bell peppers\n• Broccoli\n• Soy sauce\n• Ginger\n• Rice",
                "preparation_time": "30 minutes",
                "instructions": "1. Cook rice\n2. Stir fry beef\n3. Add vegetables\n4. Mix with sauce",
                "image_file": "https://images.unsplash.com/photo-1534939561126-855b8675edd7"
            },
            {
                "title": "Mediterranean Hummus Bowl",
                "ingredients": "• Hummus\n• Cherry tomatoes\n• Cucumber\n• Olives\n• Feta\n• Pita bread",
                "preparation_time": "15 minutes",
                "instructions": "1. Spread hummus\n2. Arrange toppings\n3. Drizzle olive oil\n4. Serve with pita",
                "image_file": "https://images.unsplash.com/photo-1540914124281-342587941389"
            },
            {
                "title": "Mango Lassi",
                "ingredients": "• Ripe mango\n• Yogurt\n• Milk\n• Cardamom\n• Honey\n• Ice",
                "preparation_time": "10 minutes",
                "instructions": "1. Blend mango\n2. Add yogurt and milk\n3. Add spices\n4. Serve chilled",
                "image_file": "https://images.unsplash.com/photo-1546173159-315724a31696"
            },
            {
                "title": "Lemon Garlic Shrimp",
                "ingredients": "• Shrimp\n• Garlic\n• Lemon\n• Butter\n• Parsley\n• White wine",
                "preparation_time": "20 minutes",
                "instructions": "1. Melt butter\n2. Cook garlic\n3. Add shrimp\n4. Finish with lemon",
                "image_file": "https://images.unsplash.com/photo-1625943553852-781c212b8079"
            },
            {
                "title": "Acai Bowl",
                "ingredients": "• Acai packet\n• Banana\n• Berries\n• Granola\n• Honey\n• Coconut",
                "preparation_time": "10 minutes",
                "instructions": "1. Blend acai\n2. Pour in bowl\n3. Add toppings\n4. Drizzle honey",
                "image_file": "https://images.unsplash.com/photo-1590301157890-4810ed352733"
            },
            {
                "title": "Veggie Wrap",
                "ingredients": "• Tortilla\n• Hummus\n• Mixed greens\n• Avocado\n• Carrots\n• Cucumber",
                "preparation_time": "15 minutes",
                "instructions": "1. Spread hummus\n2. Layer vegetables\n3. Add avocado\n4. Roll tightly",
                "image_file": "https://images.unsplash.com/photo-1600850056064-a8b380df8395"
            },
            {
                "title": "Chicken Noodle Soup",
                "ingredients": "• Chicken breast\n• Egg noodles\n• Carrots\n• Celery\n• Onion\n• Broth",
                "preparation_time": "45 minutes",
                "instructions": "1. Cook chicken\n2. Prepare vegetables\n3. Add noodles\n4. Simmer",
                "image_file": "https://images.unsplash.com/photo-1547592166-23ac45744acd"
            },
            {
                "title": "Apple Cinnamon Oatmeal",
                "ingredients": "• Rolled oats\n• Apple\n• Cinnamon\n• Honey\n• Walnuts\n• Milk",
                "preparation_time": "15 minutes",
                "instructions": "1. Cook oats\n2. Add apple\n3. Stir in cinnamon\n4. Top with nuts",
                "image_file": "https://images.unsplash.com/photo-1517673400267-0251440c45dc"
            },
            {
                "title": "Grilled Cheese Sandwich",
                "ingredients": "• Sourdough bread\n• Cheddar cheese\n• Butter\n• Optional: tomato\n• Optional: ham",
                "preparation_time": "10 minutes",
                "instructions": "1. Butter bread\n2. Add cheese\n3. Grill until golden\n4. Slice and serve",
                "image_file": "https://images.unsplash.com/photo-1528735602780-2552fd46c7af"
            },
            {
                "title": "Fresh Spring Rolls",
                "ingredients": "• Rice paper\n• Shrimp\n• Rice noodles\n• Lettuce\n• Mint\n• Peanut sauce",
                "preparation_time": "30 minutes",
                "instructions": "1. Prep ingredients\n2. Soak rice paper\n3. Roll ingredients\n4. Serve with sauce",
                "image_file": "https://images.unsplash.com/photo-1536510233921-8e5043fce771"
            },
            {
                "title": "Roasted Vegetables",
                "ingredients": "• Brussels sprouts\n• Carrots\n• Sweet potato\n• Olive oil\n• Herbs\n• Garlic",
                "preparation_time": "40 minutes",
                "instructions": "1. Cut vegetables\n2. Season well\n3. Roast at 400°F\n4. Serve hot",
                "image_file": "https://images.unsplash.com/photo-1546793665-c74683f339c1"
            },
            {
                "title": "Protein Smoothie",
                "ingredients": "• Protein powder\n• Banana\n• Almond milk\n• Peanut butter\n• Ice\n• Honey",
                "preparation_time": "5 minutes",
                "instructions": "1. Add ingredients\n2. Blend until smooth\n3. Check consistency\n4. Serve cold",
                "image_file": "https://images.unsplash.com/photo-1553530666-ba11a7da3888"
            },
            {
                "title": "Garlic Bread",
                "ingredients": "• French bread\n• Butter\n• Garlic\n• Parsley\n• Parmesan\n• Olive oil",
                "preparation_time": "15 minutes",
                "instructions": "1. Mix butter & garlic\n2. Spread on bread\n3. Bake until crispy\n4. Garnish",
                "image_file": "https://images.unsplash.com/photo-1619535860434-da906327f876"
            },
            {
                "title": "Chocolate Mousse",
                "ingredients": "• Dark chocolate\n• Heavy cream\n• Eggs\n• Sugar\n• Vanilla extract",
                "preparation_time": "3 hours",
                "instructions": "1. Melt chocolate\n2. Whip cream\n3. Fold together\n4. Chill",
                "image_file": "https://images.unsplash.com/photo-1541783245831-57d6fb0926d3"
            },
            {
                "title": "Bruschetta",
                "ingredients": "• Baguette\n• Tomatoes\n• Basil\n• Garlic\n• Olive oil\n• Balsamic",
                "preparation_time": "20 minutes",
                "instructions": "1. Toast bread\n2. Mix topping\n3. Assemble\n4. Drizzle oil",
                "image_file": "https://images.unsplash.com/photo-1572695157366-5e585ab2b69f"
            },
            {
                "title": "Fruit Salad",
                "ingredients": "• Mixed berries\n• Apple\n• Orange\n• Grapes\n• Honey\n• Mint",
                "preparation_time": "15 minutes",
                "instructions": "1. Cut fruits\n2. Combine in bowl\n3. Add honey\n4. Garnish with mint",
                "image_file": "https://images.unsplash.com/photo-1490474418585-ba9bad8fd0ea"
            },
            {
                "title": "Mushroom Soup",
                "ingredients": "• Mixed mushrooms\n• Onion\n• Garlic\n• Cream\n• Thyme\n• Stock",
                "preparation_time": "35 minutes",
                "instructions": "1. Sauté vegetables\n2. Add stock\n3. Blend smooth\n4. Add cream",
                "image_file": "https://images.unsplash.com/photo-1547592166-23ac45744acd"
            },
            {
                "title": "Energy Balls",
                "ingredients": "• Dates\n• Nuts\n• Oats\n• Cocoa powder\n• Honey\n• Coconut",
                "preparation_time": "20 minutes",
                "instructions": "1. Process dates\n2. Mix ingredients\n3. Form balls\n4. Roll in coconut",
                "image_file": "https://images.unsplash.com/photo-1604329760661-e71dc83f8f26"
            }
        ]

        for recipe_data in recipes:
            recipe = Recipe(
                title=recipe_data["title"],
                ingredients=recipe_data["ingredients"],
                preparation_time=recipe_data["preparation_time"],
                instructions=recipe_data["instructions"],
                image_file=recipe_data["image_file"],
                user_id=official.id
            )
            db.session.add(recipe)
        
        db.session.commit()
        return "Official account and recipes created!"
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
