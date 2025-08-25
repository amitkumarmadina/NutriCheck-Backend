from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import os
import io
import re
import uuid
import json
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image
import asyncio
import random

app = FastAPI(
    title="Health Awareness Label Scanner API",
    description="Scan food labels and analyze ingredient health risks",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB connection
client = None
db = None

# Response models
class IngredientAnalysis(BaseModel):
    name: str
    risk_level: str
    description: str
    banned_in: Optional[Dict[str, bool]] = {}
    sources: Optional[List[str]] = []
    confidence: float = 0.0

class ScanResult(BaseModel):
    scan_id: str
    ocr_text: str
    parsed_ingredients: List[IngredientAnalysis]
    processing_time: float
    image_info: Dict[str, Any]
    nutritional_info: Optional[Dict[str, str]] = None

# Sample ingredients database - this would be populated from MongoDB
SAMPLE_INGREDIENTS = {
    # ✅ Safe ingredients (Green)
    "water": {
        "name": "Water",
        "risk_level": "safe",
        "description": "Essential nutrient, completely safe for consumption",
        "sources": ["WHO", "FDA"]
    },
    "sugar": {
        "name": "Sugar",
        "risk_level": "safe",
        "description": "Natural sweetener, safe in moderation",
        "sources": ["FDA"]
    },
    "salt": {
        "name": "Salt",
        "risk_level": "safe", 
        "description": "Sodium chloride, essential mineral when consumed in moderation",
        "sources": ["FDA"]
    },
    "flour": {
        "name": "Flour",
        "risk_level": "safe",
        "description": "Wheat flour, safe for most people except those with celiac disease",
        "sources": ["FDA"]
    },
    "milk": {
        "name": "Milk",
        "risk_level": "safe",
        "description": "Dairy product, safe for lactose-tolerant individuals",
        "sources": ["FDA", "USDA"]
    },
    "eggs": {
        "name": "Eggs",
        "risk_level": "safe",
        "description": "High-quality protein source, safe when properly cooked",
        "sources": ["FDA", "USDA"]
    },
    "honey": {
        "name": "Honey",
        "risk_level": "safe",
        "description": "Natural sweetener with antioxidants, safe for most people (not for infants under 1 year)",
        "sources": ["FDA", "WHO"]
    },
    "olive oil": {
        "name": "Olive Oil",
        "risk_level": "safe",
        "description": "Healthy fat, rich in monounsaturated fatty acids",
        "sources": ["American Heart Association"]
    },
    "rice": {
        "name": "Rice",
        "risk_level": "safe",
        "description": "Staple grain, safe when cooked properly",
        "sources": ["USDA"]
    },
    "oats": {
        "name": "Oats",
        "risk_level": "safe",
        "description": "Whole grain high in fiber and beneficial for heart health",
        "sources": ["FDA", "USDA"]
    },
    "coconut oil": {
        "name": "Coconut Oil",
        "risk_level": "safe",
        "description": "Natural fat, stable for cooking, contains medium-chain triglycerides",
        "sources": ["FDA"]
    },

    # ⚠️ Caution ingredients (Yellow)
    "sodium benzoate": {
        "name": "Sodium Benzoate",
        "risk_level": "caution",
        "description": "Preservative that may cause allergic reactions in sensitive individuals",
        "sources": ["FDA", "European Food Safety Authority"]
    },
    "high fructose corn syrup": {
        "name": "High Fructose Corn Syrup",
        "risk_level": "caution",
        "description": "Sweetener linked to obesity and metabolic issues when consumed in excess",
        "sources": ["American Heart Association", "Mayo Clinic"]
    },
    "monosodium glutamate": {
        "name": "Monosodium Glutamate (MSG)",
        "risk_level": "caution",
        "description": "Flavor enhancer that may cause headaches in sensitive individuals",
        "sources": ["FDA"]
    },
    "artificial vanilla": {
        "name": "Artificial Vanilla",
        "risk_level": "caution",
        "description": "Synthetic flavoring, generally safe but some prefer natural alternatives",
        "sources": ["FDA"]
    },
    "potassium sorbate": {
        "name": "Potassium Sorbate",
        "risk_level": "caution",
        "description": "Preservative that may cause skin irritation in sensitive individuals",
        "sources": ["FDA"]
    },
    "aspartame": {
        "name": "Aspartame",
        "risk_level": "caution",
        "description": "Artificial sweetener, not recommended for people with PKU condition",
        "sources": ["FDA", "WHO"]
    },
    "acesulfame k": {
        "name": "Acesulfame Potassium (Ace-K)",
        "risk_level": "caution",
        "description": "Artificial sweetener, safe within limits but controversial for long-term effects",
        "sources": ["FDA"]
    },
    "saccharin": {
        "name": "Saccharin",
        "risk_level": "caution",
        "description": "Artificial sweetener, allowed but linked to bladder cancer in animal studies",
        "sources": ["FDA", "National Cancer Institute"]
    },
    "sodium nitrate": {
        "name": "Sodium Nitrate",
        "risk_level": "caution",
        "description": "Used in processed meats, may form carcinogenic nitrosamines when cooked at high heat",
        "sources": ["WHO", "FDA"]
    },

    # ⛔ Banned ingredients (Red)
    "red dye 40": {
        "name": "Red Dye 40",
        "risk_level": "banned",
        "description": "Artificial coloring banned in several countries due to hyperactivity concerns",
        "banned_in": {"EU": True, "Norway": True, "Austria": True},
        "sources": ["European Food Safety Authority", "Center for Science in Public Interest"]
    },
    "yellow dye 6": {
        "name": "Yellow Dye 6",
        "risk_level": "banned",
        "description": "Artificial coloring banned in some countries, linked to hyperactivity",
        "banned_in": {"Norway": True, "Finland": True},
        "sources": ["European Food Safety Authority"]
    },
    "bha": {
        "name": "BHA (Butylated Hydroxyanisole)",
        "risk_level": "banned",
        "description": "Preservative classified as possible carcinogen, banned in some countries",
        "banned_in": {"EU": True, "Japan": True},
        "sources": ["International Agency for Research on Cancer"]
    },
    "bht": {
        "name": "BHT (Butylated Hydroxytoluene)",
        "risk_level": "banned",
        "description": "Preservative banned in several countries due to health concerns",
        "banned_in": {"EU": True, "Australia": True, "New Zealand": True},
        "sources": ["European Food Safety Authority"]
    },
    "trans fat": {
        "name": "Trans Fat",
        "risk_level": "banned",
        "description": "Artificial trans fats banned due to cardiovascular health risks",
        "banned_in": {"US": True, "Canada": True, "EU": True},
        "sources": ["WHO", "FDA", "American Heart Association"]
    },
    "brominated vegetable oil": {
        "name": "Brominated Vegetable Oil (BVO)",
        "risk_level": "banned",
        "description": "Emulsifier banned in EU and Japan, linked to reproductive and thyroid issues",
        "banned_in": {"EU": True, "Japan": True, "India": True},
        "sources": ["FDA", "EFSA"]
    },
    "azodicarbonamide": {
        "name": "Azodicarbonamide",
        "risk_level": "banned",
        "description": "Used in bread as a dough conditioner, banned in EU and Australia",
        "banned_in": {"EU": True, "Australia": True, "Singapore": True},
        "sources": ["European Food Safety Authority", "FDA"]
    },
    "ractopamine": {
        "name": "Ractopamine",
        "risk_level": "banned",
        "description": "Feed additive banned in EU, China, and Russia due to safety concerns",
        "banned_in": {"EU": True, "China": True, "Russia": True},
        "sources": ["FAO", "EFSA"]
    }
}


class MockOCRService:
    """Mock OCR service to simulate Google Cloud Vision API"""
    
    def __init__(self):
        self.mock_responses = [
            """NUTRITION FACTS
Serving Size 1 package (40g)
Calories 150
Total Fat 6g
Saturated Fat 3g
Trans Fat 0g
Cholesterol 10mg
Sodium 125mg
Total Carbohydrate 20g
Dietary Fiber 2g
Total Sugars 12g
Protein 4g

INGREDIENTS: Wheat flour, sugar, palm oil, milk powder, eggs, salt, baking powder, artificial vanilla, sodium benzoate, red dye 40""",
            
            """INGREDIENTS: Water, high fructose corn syrup, citric acid, natural flavors, sodium benzoate, potassium sorbate, yellow dye 6, caffeine
            
NUTRITION INFORMATION
Calories per serving: 140
Total Fat: 0g
Sodium: 55mg
Total Carbs: 39g
Sugars: 38g""",
            
            """INGREDIENTS: Milk, sugar, cocoa, vanilla extract, carrageenan, guar gum
            
NUTRITIONAL INFORMATION (per 100ml):
Energy: 280kJ/67kcal
Fat: 2.1g
Saturated Fat: 1.3g
Carbohydrates: 9.8g
Sugars: 9.7g
Protein: 3.4g
Salt: 0.1g""",
            
            """INGREDIENTS: Wheat flour, water, yeast, salt, sugar, vegetable oil, milk powder, eggs, BHA, BHT
            
NUTRITION FACTS
Serving Size: 2 slices (60g)
Calories: 160
Total Fat: 2g
Sodium: 320mg
Total Carbohydrate: 30g
Dietary Fiber: 2g
Protein: 6g""",
            
            """ORGANIC INGREDIENTS: Organic wheat flour, organic sugar, organic palm oil, organic milk, organic eggs, sea salt, organic vanilla extract, baking soda
            
NUTRITION INFORMATION
Per serving (30g):
Energy: 520kJ/125kcal
Fat: 5.2g
Saturated Fat: 2.8g
Carbohydrates: 18g
Sugars: 6.5g
Protein: 2.1g
Salt: 0.4g""",

            """INGREDIENTS: Brown rice, water, coconut oil, sea salt, nutritional yeast
            
NUTRITION FACTS
Serving Size: 1 cup (150g)
Calories: 180
Total Fat: 4g
Sodium: 200mg
Total Carbohydrate: 35g
Dietary Fiber: 3g
Protein: 4g"""
        ]
    
    async def extract_text_from_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Simulate OCR text extraction"""
        # Simulate processing time
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # Return a random mock response
        mock_text = random.choice(self.mock_responses)
        
        return {
            "extracted_text": mock_text,
            "confidence": random.uniform(0.85, 0.98),
            "processing_method": "mock_ocr"
        }

# Initialize mock OCR service
mock_ocr = MockOCRService()

@app.on_event("startup")
async def startup_db_client():
    """Initialize database connection"""
    global client, db
    try:
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        client = AsyncIOMotorClient(mongo_url)
        db = client.health_scanner
        
        # Initialize sample data
        await initialize_sample_data()
        print("Database connected and initialized successfully")
    except Exception as e:
        print(f"Database connection failed: {e}")

@app.on_event("shutdown")
async def shutdown_db_client():
    """Close database connection"""
    if client:
        client.close()

async def initialize_sample_data():
    """Initialize sample ingredients in database"""
    try:
        # Clear existing data
        await db.ingredients.delete_many({})
        
        # Insert sample ingredients
        ingredients_data = []
        for key, ingredient in SAMPLE_INGREDIENTS.items():
            ingredient_doc = {
                "_id": str(uuid.uuid4()),
                "name": ingredient["name"],
                "synonyms": [key, ingredient["name"].lower()],
                "description": ingredient["description"],
                "risk_level": ingredient["risk_level"],
                "banned_in": ingredient.get("banned_in", {}),
                "sources": ingredient.get("sources", []),
                "created_at": datetime.utcnow()
            }
            ingredients_data.append(ingredient_doc)
        
        await db.ingredients.insert_many(ingredients_data)
        print(f"Inserted {len(ingredients_data)} sample ingredients")
        
    except Exception as e:
        print(f"Error initializing sample data: {e}")

def parse_ingredients_from_text(text: str) -> List[str]:
    """Extract ingredients list from OCR text"""
    # Look for ingredients section
    ingredients_match = re.search(r"ingredients?\s*:?\s*(.+?)(?:\n\n|nutrition|$)", text.lower(), re.DOTALL | re.IGNORECASE)
    
    if ingredients_match:
        ingredients_text = ingredients_match.group(1)
        # Split by commas and clean up
        ingredients = [ingredient.strip() for ingredient in ingredients_text.split(",")]
        # Filter out empty strings and very short entries
        ingredients = [ing for ing in ingredients if len(ing) > 2]
        return ingredients
    
    return []

def extract_nutritional_info(text: str) -> Dict[str, str]:
    """Extract nutritional information from OCR text"""
    nutritional_info = {}
    
    # Define patterns for common nutritional elements
    patterns = {
        "calories": r"calories?\s*:?\s*(\d+)",
        "total_fat": r"total\s*fat\s*:?\s*(\d+\.?\d*)\s*g",
        "saturated_fat": r"saturated\s*fat\s*:?\s*(\d+\.?\d*)\s*g",
        "trans_fat": r"trans\s*fat\s*:?\s*(\d+\.?\d*)\s*g",
        "cholesterol": r"cholesterol\s*:?\s*(\d+)\s*mg",
        "sodium": r"sodium\s*:?\s*(\d+)\s*mg",
        "total_carbs": r"total\s*carbohydrate\s*:?\s*(\d+)\s*g",
        "fiber": r"dietary\s*fiber\s*:?\s*(\d+)\s*g",
        "sugars": r"(?:total\s*)?sugars?\s*:?\s*(\d+\.?\d*)\s*g",
        "protein": r"protein\s*:?\s*(\d+\.?\d*)\s*g"
    }
    
    for nutrient, pattern in patterns.items():
        match = re.search(pattern, text.lower())
        if match:
            nutritional_info[nutrient] = match.group(1)
    
    return nutritional_info

async def analyze_ingredients(ingredient_names: List[str]) -> List[IngredientAnalysis]:
    """Analyze ingredients against database"""
    analyzed_ingredients = []
    
    for ingredient_name in ingredient_names:
        ingredient_lower = ingredient_name.lower().strip()
        
        # Try to find in database first
        db_ingredient = None
        if db is not None:
            try:
                db_ingredient = await db.ingredients.find_one({
                    "$or": [
                        {"synonyms": {"$in": [ingredient_lower]}},
                        {"name": {"$regex": ingredient_lower, "$options": "i"}}
                    ]
                })
            except Exception as e:
                print(f"Database query error: {e}")
        
        if db_ingredient:
            # Found in database
            analysis = IngredientAnalysis(
                name=db_ingredient["name"],
                risk_level=db_ingredient["risk_level"],
                description=db_ingredient["description"],
                banned_in=db_ingredient.get("banned_in", {}),
                sources=db_ingredient.get("sources", []),
                confidence=0.95
            )
        else:
            # Check in sample data as fallback
            found_ingredient = None
            for key, ingredient in SAMPLE_INGREDIENTS.items():
                if key in ingredient_lower or ingredient_lower in key:
                    found_ingredient = ingredient
                    break
            
            if found_ingredient:
                analysis = IngredientAnalysis(
                    name=found_ingredient["name"],
                    risk_level=found_ingredient["risk_level"],
                    description=found_ingredient["description"],
                    banned_in=found_ingredient.get("banned_in", {}),
                    sources=found_ingredient.get("sources", []),
                    confidence=0.85
                )
            else:
                # Unknown ingredient - default to safe
                analysis = IngredientAnalysis(
                    name=ingredient_name.title(),
                    risk_level="safe",
                    description="Ingredient not found in database. Generally considered safe.",
                    confidence=0.3
                )
        
        analyzed_ingredients.append(analysis)
    
    return analyzed_ingredients

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "Health Awareness Label Scanner API is running", "version": "1.0.0"}

@app.post("/api/scan", response_model=ScanResult)
async def scan_food_label(file: UploadFile = File(...)):
    """
    Scan food label image and analyze ingredients
    """
    start_time = datetime.now()
    
    try:
        # Validate file
        if not file.content_type or not file.content_type.startswith('image/'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only image files are allowed"
            )
        
        # Check file size (10MB limit)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File size exceeds 10MB limit"
            )
        
        # Validate image
        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid image file"
            )
        
        # Extract text using mock OCR
        ocr_result = await mock_ocr.extract_text_from_image(content)
        extracted_text = ocr_result["extracted_text"]
        
        # Parse ingredients from text
        ingredient_names = parse_ingredients_from_text(extracted_text)
        
        # Extract nutritional information
        nutritional_info = extract_nutritional_info(extracted_text)
        
        # Analyze ingredients
        analyzed_ingredients = await analyze_ingredients(ingredient_names)
        
        # Generate scan ID
        scan_id = str(uuid.uuid4())
        
        # Calculate processing time
        processing_time = (datetime.now() - start_time).total_seconds()
        
        # Store scan result in database
        scan_document = {
            "_id": scan_id,
            "image_filename": file.filename,
            "image_size": len(content),
            "ocr_text": extracted_text,
            "parsed_ingredients": [ingredient.dict() for ingredient in analyzed_ingredients],
            "nutritional_info": nutritional_info,
            "processing_time": processing_time,
            "created_at": datetime.utcnow()
        }
        
        if db is not None:
            try:
                await db.scans.insert_one(scan_document)
            except Exception as e:
                print(f"Error storing scan result: {e}")
        
        return ScanResult(
            scan_id=scan_id,
            ocr_text=extracted_text,
            parsed_ingredients=analyzed_ingredients,
            processing_time=processing_time,
            nutritional_info=nutritional_info,
            image_info={
                "filename": file.filename,
                "size": len(content),
                "content_type": file.content_type
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/ingredients")
async def get_all_ingredients():
    """Get all ingredients from database"""
    try:
        if db is None:
            return {"ingredients": list(SAMPLE_INGREDIENTS.values())}
        
        cursor = db.ingredients.find({})
        ingredients = await cursor.to_list(length=100)
        
        # Convert MongoDB ObjectId to string for JSON serialization
        for ingredient in ingredients:
            ingredient["id"] = ingredient.pop("_id")
        
        return {"ingredients": ingredients}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving ingredients: {str(e)}"
        )

@app.get("/api/scans")
async def get_scan_history(limit: int = 10):
    """Get recent scan history"""
    try:
        if db is None:
            return {"scans": []}
        
        cursor = db.scans.find({}).sort("created_at", -1).limit(limit)
        scans = await cursor.to_list(length=limit)
        
        # Convert MongoDB ObjectId to string for JSON serialization
        for scan in scans:
            scan["id"] = scan.pop("_id")
        
        return {"scans": scans}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving scan history: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)