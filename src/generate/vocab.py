"""Fixed word lists backing the synthetic retail dataset.

Kept in one module and never reordered: the generator indexes into these lists
with a seeded RNG, so changing the order changes every downstream value and
breaks the byte-for-byte reproducibility guarantee.
"""

FIRST_NAMES = [
    "Aarav", "Ada", "Amara", "Anders", "Ana", "Bilal", "Camila", "Chen",
    "Daniela", "Dmitri", "Elena", "Emeka", "Fatima", "Felix", "Gabriel",
    "Grace", "Hana", "Hiroshi", "Ingrid", "Isabel", "Jonas", "Julia",
    "Kwame", "Lars", "Leila", "Lucas", "Maria", "Mateo", "Nadia", "Niall",
    "Olga", "Omar", "Priya", "Rafael", "Rania", "Rohan", "Sofia", "Sven",
    "Tariq", "Thandi", "Uma", "Viktor", "Wei", "Yara", "Yusuf", "Zara",
]

LAST_NAMES = [
    "Abiola", "Almeida", "Andersson", "Bauer", "Bianchi", "Chen", "Cohen",
    "Costa", "Dubois", "Eriksen", "Ferrari", "Fischer", "Garcia", "Haddad",
    "Ibrahim", "Iyer", "Jansen", "Kaur", "Kowalski", "Larsen", "Lopez",
    "Mbeki", "Mehta", "Moreau", "Muller", "Nakamura", "Nowak", "Okafor",
    "Oliveira", "Petrov", "Rossi", "Santos", "Silva", "Sharma", "Suzuki",
    "Tanaka", "Torres", "Novak", "Weber", "Yilmaz",
]

# Weighted so the country dimension is skewed like a real customer base -
# a uniform distribution makes every GROUP BY look suspiciously flat.
COUNTRIES = [
    ("US", 30), ("IN", 14), ("GB", 10), ("DE", 8), ("BR", 7), ("CA", 6),
    ("FR", 5), ("AU", 4), ("JP", 4), ("NG", 3), ("ES", 3), ("MX", 3),
    ("ZA", 2), ("SE", 1),
]

EMAIL_DOMAINS = [
    "example.com", "mail.example.net", "shop.example.org", "example.co",
]

CATEGORIES = [
    "Electronics", "Home & Kitchen", "Apparel", "Sports & Outdoors",
    "Beauty", "Toys & Games", "Grocery", "Office", "Automotive", "Books",
]

# Per-category price band (min, max) in USD. Gives the price column a shape
# that actually varies by category, which makes the Athena aggregations and
# the Redshift star schema queries produce interesting results.
CATEGORY_PRICE_BANDS = {
    "Electronics": (29.99, 1899.00),
    "Home & Kitchen": (8.50, 449.00),
    "Apparel": (9.99, 249.00),
    "Sports & Outdoors": (12.00, 899.00),
    "Beauty": (4.99, 129.00),
    "Toys & Games": (6.99, 199.00),
    "Grocery": (1.49, 79.00),
    "Office": (2.99, 599.00),
    "Automotive": (7.99, 999.00),
    "Books": (5.99, 89.00),
}

PRODUCT_ADJECTIVES = [
    "Compact", "Premium", "Everyday", "Pro", "Classic", "Ultra", "Eco",
    "Smart", "Heavy-Duty", "Lightweight", "Deluxe", "Essential", "Portable",
    "Wireless", "Rechargeable", "Stainless", "Organic", "Adjustable",
]

PRODUCT_NOUNS = {
    "Electronics": ["Headphones", "Monitor", "Keyboard", "Router", "Speaker", "Charger", "Tablet", "Webcam"],
    "Home & Kitchen": ["Blender", "Kettle", "Cookware Set", "Lamp", "Vacuum", "Air Fryer", "Mattress"],
    "Apparel": ["Jacket", "Sneakers", "T-Shirt", "Jeans", "Hoodie", "Scarf", "Boots"],
    "Sports & Outdoors": ["Tent", "Yoga Mat", "Dumbbell", "Bicycle", "Backpack", "Kayak", "Helmet"],
    "Beauty": ["Serum", "Shampoo", "Moisturizer", "Lipstick", "Razor", "Perfume"],
    "Toys & Games": ["Puzzle", "Board Game", "Building Blocks", "Drone", "Action Figure"],
    "Grocery": ["Coffee Beans", "Olive Oil", "Pasta", "Green Tea", "Honey", "Granola"],
    "Office": ["Desk Chair", "Notebook", "Printer", "Whiteboard", "Desk Lamp", "Shredder"],
    "Automotive": ["Dash Cam", "Tyre Inflator", "Car Cover", "Jump Starter", "Roof Rack"],
    "Books": ["Novel", "Cookbook", "Atlas", "Biography", "Field Guide", "Textbook"],
}

# Weighted order status. Most orders complete; a realistic tail of
# cancelled/returned rows gives the ETL filters something real to remove.
ORDER_STATUSES = [
    ("DELIVERED", 62), ("SHIPPED", 15), ("PENDING", 10),
    ("CANCELLED", 7), ("RETURNED", 6),
]
