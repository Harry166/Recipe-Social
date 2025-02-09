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

def get_recipe_recommendations(mood, recipes, num_recommendations=10):
    if not recipes:
        return []
        
    recipe_texts = []
    for recipe in recipes:
        text = f"{recipe.title} {recipe.ingredients} {recipe.instructions}"
        recipe_texts.append(text.lower())
    
    mood_keywords = " ".join(MOOD_MAPPINGS.get(mood, []))
    all_texts = recipe_texts + [mood_keywords]
    
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(all_texts)
    
    mood_vector = tfidf_matrix[-1]
    recipe_vectors = tfidf_matrix[:-1]
    similarities = cosine_similarity(recipe_vectors, mood_vector)
    
    top_indices = similarities.flatten().argsort()[-num_recommendations:][::-1]
    return [recipes[i] for i in top_indices]

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
        # First, delete all existing recipes from Recipe Social account
        official = User.query.filter_by(username="Recipe Social").first()
        if official:
            # Delete all existing recipes for this user
            Recipe.query.filter_by(user_id=official.id).delete()
            db.session.commit()
        else:
            # Create the official account if it doesn't exist
            official = User(
                username="Recipe Social",
                email="official@recipesocial.com",
                password=generate_password_hash("securepassword123"),
                bio="The official Recipe Social account.",
                profile_pic="https://images.unsplash.com/photo-1556911261-6bd341186b2f"
            )
            db.session.add(official)
            db.session.commit()

        # Then add all the recipes (rest of your code remains the same)
        recipes = [
            {
                "title": "Classic Homemade Pizza",
                "ingredients": "• 2 1/4 cups bread flour\n• 1 1/2 tsp instant yeast\n• 1 1/2 tsp salt\n• 1 cup warm water (110°F)\n• 2 tbsp olive oil\n• 1 cup pizza sauce\n• 2 cups mozzarella cheese, shredded\n• 1/4 cup fresh basil leaves\n• Optional toppings: pepperoni, mushrooms, bell peppers",
                "preparation_time": "1 hour 30 minutes",
                "instructions": "1. In a large bowl, combine flour, yeast, and salt\n2. Add warm water and olive oil, mix until a shaggy dough forms\n3. Knead dough for 10 minutes until smooth and elastic\n4. Place in oiled bowl, cover with damp cloth\n5. Let rise in warm place for 1 hour or until doubled\n6. Preheat oven to 450°F with pizza stone if available\n7. Punch down dough and roll into 14-inch circle\n8. Transfer to parchment paper or cornmeal-dusted pan\n9. Spread sauce evenly, leaving 1/2 inch border\n10. Top with cheese and desired toppings\n11. Bake 12-15 minutes until crust is golden\n12. Garnish with fresh basil\n13. Let cool 5 minutes before slicing",
                "image_file": "https://images.unsplash.com/photo-1513104890138-7c749659a591"
            },
            {
                "title": "Creamy Mac and Cheese",
                "ingredients": "• 1 pound elbow macaroni\n• 4 cups sharp cheddar cheese, freshly grated\n• 2 cups whole milk\n• 1/4 cup unsalted butter\n• 1/4 cup all-purpose flour\n• 1 tsp salt\n• 1/2 tsp black pepper\n• 1/4 tsp garlic powder\n• 1/8 tsp cayenne pepper (optional)\n• 1/4 cup breadcrumbs\n• 2 tbsp fresh parsley, chopped",
                "preparation_time": "35 minutes",
                "instructions": "1. Bring large pot of salted water to boil\n2. Cook macaroni 2 minutes less than package directions\n3. Meanwhile, melt butter in large saucepan over medium heat\n4. Whisk in flour and cook 1 minute, stirring constantly\n5. Gradually whisk in milk, cook until thickened (3-4 minutes)\n6. Reduce heat to low, add cheese in batches, stirring until melted\n7. Season with salt, pepper, garlic powder, and cayenne if using\n8. Drain pasta (don't rinse), return to pot\n9. Pour cheese sauce over pasta, mix thoroughly\n10. Transfer to baking dish if desired\n11. Top with breadcrumbs\n12. Optional: broil 2-3 minutes until golden\n13. Garnish with parsley and serve hot",
                "image_file": "https://images.unsplash.com/photo-1543339494-b4cd4f7ba686"
            },
            {
                "title": "Fresh Garden Salad",
                "ingredients": "• 6 cups mixed salad greens\n• 1 cup cherry tomatoes, halved\n• 1 English cucumber, thinly sliced\n• 1/2 red onion, thinly sliced\n• 1 avocado, diced\n• 1/4 cup extra virgin olive oil\n• 2 tbsp balsamic vinegar\n• 1 tsp Dijon mustard\n• 1 clove garlic, minced\n• 1/2 tsp honey\n• Salt and pepper to taste\n• Optional: croutons, sunflower seeds",
                "preparation_time": "15 minutes",
                "instructions": "1. Wash and thoroughly dry all greens and vegetables\n2. Tear lettuce into bite-sized pieces if needed\n3. Halve cherry tomatoes\n4. Slice cucumber into thin rounds\n5. Soak sliced onion in cold water for 5 minutes to reduce sharpness\n6. Make dressing: whisk olive oil, vinegar, mustard, garlic, and honey\n7. Season dressing with salt and pepper to taste\n8. Drain onions and pat dry\n9. In large bowl, combine greens, tomatoes, cucumber, and onion\n10. Dice avocado just before serving\n11. Drizzle with dressing and toss gently\n12. Top with croutons and seeds if using\n13. Serve immediately",
                "image_file": "https://images.unsplash.com/photo-1512621776951-a57141f2eefd"
            },
            {
                "title": "Breakfast Smoothie Bowl",
                "ingredients": "• 2 frozen bananas, chunked\n• 1 cup frozen mixed berries\n• 1 cup Greek yogurt\n• 1/4 cup almond milk\n• 1 tbsp honey\n• 1 tbsp chia seeds\nToppings:\n• 1/4 cup granola\n• Fresh berries\n• Sliced banana\n• 1 tbsp almond butter\n• Coconut flakes\n• Extra honey for drizzling",
                "preparation_time": "15 minutes",
                "instructions": "1. Place frozen bananas and berries in high-speed blender\n2. Add yogurt, almond milk, and honey\n3. Blend until smooth but still thick (consistency should be thicker than drinkable)\n4. Add more milk 1 tbsp at a time if needed to blend\n5. Stir in chia seeds\n6. Pour into chilled bowl\n7. Arrange toppings in sections:\n   - Place granola on one side\n   - Add fresh berries in another section\n   - Fan out banana slices\n   - Drizzle with almond butter\n   - Sprinkle coconut flakes\n8. Finish with honey drizzle\n9. Serve immediately while cold",
                "image_file": "https://images.unsplash.com/photo-1511690743698-d9d85f2fbf38"
            },
            {
                "title": "Grilled Salmon",
                "ingredients": "• 4 (6 oz) salmon fillets\n• 2 tbsp olive oil\n• 4 cloves garlic, minced\n• 1 lemon, juiced and zested\n• 2 tbsp fresh dill, chopped\n• 1 tsp salt\n• 1/2 tsp black pepper\n• 1/4 tsp red pepper flakes (optional)\n• Lemon wedges for serving",
                "preparation_time": "25 minutes",
                "instructions": "1. Pat salmon fillets dry with paper towels\n2. In small bowl, combine olive oil, garlic, lemon juice, zest, and dill\n3. Season fillets with salt and pepper on both sides\n4. Brush with oil mixture, let marinate 10 minutes\n5. Preheat grill to medium-high (400°F)\n6. Clean and oil grill grates well\n7. Place salmon skin-side up on grill\n8. Cook 4-5 minutes until grill marks appear\n9. Carefully flip using wide spatula\n10. Cook 3-4 minutes more until fish flakes easily\n11. Let rest 5 minutes before serving\n12. Garnish with extra dill and lemon wedges",
                "image_file": "https://images.unsplash.com/photo-1485921325833-c519f76c4927"
            },
            {
                "title": "Banana Bread",
                "ingredients": "• 3 very ripe bananas, mashed\n• 1/3 cup unsalted butter, melted\n• 1/2 cup granulated sugar\n• 1 large egg, room temperature\n• 1 tsp vanilla extract\n• 1 tsp baking soda\n• 1/4 tsp salt\n• 1 1/2 cups all-purpose flour\n• 1/4 tsp ground cinnamon\n• 1/2 cup chopped walnuts (optional)\n• 1/4 cup chocolate chips (optional)",
                "preparation_time": "1 hour 15 minutes",
                "instructions": "1. Preheat oven to 350°F (175°C)\n2. Grease a 4x8-inch loaf pan, line with parchment\n3. Mash bananas thoroughly in large bowl until no large chunks remain\n4. Mix in melted butter until completely combined\n5. Stir in sugar, beaten egg, and vanilla until well blended\n6. Sprinkle baking soda and salt over mixture\n7. Add cinnamon and stir to combine\n8. Fold in flour gently, mixing just until no dry streaks remain\n9. Fold in nuts and/or chocolate chips if using\n10. Pour batter into prepared pan, smooth top\n11. Bake 50-60 minutes until toothpick comes out clean\n12. Cool in pan 10 minutes on wire rack\n13. Remove from pan and cool completely\n14. Store wrapped tightly at room temperature",
                "image_file": "https://images.unsplash.com/photo-1621994153189-6223b41f7912"
            },
            {
                "title": "Mediterranean Quinoa Bowl",
                "ingredients": "• 1 cup quinoa, rinsed\n• 2 cups vegetable broth\n• 1 (15 oz) can chickpeas, drained and rinsed\n• 2 cups cherry tomatoes, halved\n• 1 cucumber, diced\n• 1/2 red onion, finely chopped\n• 1/2 cup kalamata olives, pitted\n• 1/2 cup feta cheese, crumbled\n• 1/4 cup fresh parsley, chopped\nDressing:\n• 3 tbsp olive oil\n• 2 tbsp lemon juice\n• 2 cloves garlic, minced\n• 1 tsp dried oregano\n• Salt and pepper to taste",
                "preparation_time": "30 minutes",
                "instructions": "1. Rinse quinoa thoroughly in fine mesh strainer\n2. Bring broth to boil in medium saucepan\n3. Add quinoa, reduce heat to low\n4. Cover and simmer 18-20 minutes\n5. Remove from heat, let stand 5 minutes\n6. Fluff with fork, let cool slightly\n7. Meanwhile, prepare vegetables:\n   - Halve tomatoes\n   - Dice cucumber into 1/2 inch pieces\n   - Finely chop onion\n   - Drain and rinse chickpeas\n8. Make dressing:\n   - Whisk olive oil, lemon juice, garlic\n   - Add oregano, salt, and pepper\n9. In large bowl, combine quinoa and vegetables\n10. Add olives and chickpeas\n11. Pour dressing over, toss gently\n12. Top with feta and parsley\n13. Serve at room temperature or chilled",
                "image_file": "https://images.unsplash.com/photo-1543362906-acfc16c67564"
            },
            {
                "title": "Garlic Bread",
                "ingredients": "• 1 French baguette\n• 1/2 cup unsalted butter, softened\n• 4 cloves garlic, finely minced\n• 2 tbsp fresh parsley, finely chopped\n• 1/4 cup freshly grated Parmesan cheese\n• 1/4 tsp salt\n• 1/8 tsp black pepper\n• 1/4 tsp Italian seasoning (optional)\n• Pinch of red pepper flakes (optional)",
                "preparation_time": "20 minutes",
                "instructions": "1. Preheat oven to 400°F (200°C)\n2. Cut baguette in half lengthwise\n3. In a bowl, mix softened butter until creamy\n4. Add minced garlic, mix well\n5. Stir in parsley, Parmesan, salt, and pepper\n6. Add Italian seasoning and red pepper if using\n7. Spread mixture evenly on both bread halves\n8. Place on baking sheet, cut sides up\n9. Bake 10-12 minutes until edges are golden\n10. Optional: broil 1-2 minutes for extra crispiness\n11. Let cool 2-3 minutes\n12. Slice diagonally into 2-inch pieces\n13. Serve immediately while warm",
                "image_file": "https://images.unsplash.com/photo-1608198093002-ad4e005484ec"
            },
            {
                "title": "Lemon Garlic Shrimp",
                "ingredients": "• 1 lb large shrimp (16-20 count), peeled and deveined\n• 4 cloves garlic, minced\n• 1 lemon, juiced and zested\n• 4 tbsp unsalted butter\n• 2 tbsp olive oil\n• 1/4 cup dry white wine\n• 1/4 cup fresh parsley, chopped\n• 1/2 tsp salt\n• 1/4 tsp black pepper\n• 1/4 tsp red pepper flakes (optional)\n• Extra lemon wedges for serving",
                "preparation_time": "20 minutes",
                "instructions": "1. Pat shrimp completely dry with paper towels\n2. Season shrimp with salt and pepper\n3. Heat olive oil and 2 tbsp butter in large skillet over medium-high\n4. Add garlic, cook 30 seconds until fragrant\n5. Add shrimp in single layer (don't crowd)\n6. Cook 2-3 minutes until bottom side is pink\n7. Flip each shrimp, cook 1-2 minutes more\n8. Remove shrimp to a plate\n9. Add wine to pan, simmer 2 minutes\n10. Add lemon juice and zest\n11. Stir in remaining butter until melted\n12. Return shrimp to pan\n13. Add parsley and red pepper flakes if using\n14. Toss until shrimp is coated and heated through\n15. Serve immediately with lemon wedges\n16. Optional: serve over pasta or rice",
                "image_file": "https://images.unsplash.com/photo-1559742811-822873691df8"
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
        return "Official account and recipes created/updated!"
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
        if not mood:
            return jsonify([])
            
        all_recipes = Recipe.query.all()
        recommended_recipes = get_recipe_recommendations(mood, all_recipes)
        
        return jsonify([{
            'id': recipe.id,
            'title': recipe.title,
            'image_file': recipe.image_file,
            'preparation_time': recipe.preparation_time,
            'views': recipe.views,
            'has_been_top': recipe.has_been_top,
            'date_posted': recipe.date_posted.strftime('%B %d, %Y'),
            'likes': len(recipe.liked_by.all())
        } for recipe in recommended_recipes])
    except Exception as e:
        print(f"Error in get_mood_recipes: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route("/sources")
def sources():
    return render_template('sources.html')

@app.route("/create_recipe", methods=['GET', 'POST'])
@login_required
def create_recipe():
    try:
        if request.method == 'POST':
            title = request.form.get('title')
            # Get all ingredients and join them with newlines
            ingredients = '\n'.join(request.form.getlist('ingredients[]'))
            instructions = request.form.get('instructions')
            preparation_time = request.form.get('preparation_time')
            
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
