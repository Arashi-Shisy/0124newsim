# c:\0124newSIm\src\name_generator.py
import random

# 日本人の姓 (Top 100)
LAST_NAMES = [
    "佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤",
    "吉田", "山田", "佐々木", "山口", "松本", "井上", "木村", "林", "斎藤", "清水",
    "山崎", "森", "池田", "橋本", "阿部", "石川", "山下", "中島", "石井", "小川",
    "前田", "岡田", "長谷川", "藤田", "後藤", "近藤", "村上", "遠藤", "青木", "坂本",
    "斉藤", "福田", "太田", "西村", "藤井", "金子", "和田", "中山", "麻生", "豊田",
    "本田", "松下", "岩崎", "三井", "住友", "安田", "渋沢", "五代", "大隈", "福沢",
    "北村", "武田", "上田", "杉山", "小山", "大塚", "平野", "菊地", "千葉", "久保",
    "松田", "野口", "中野", "原", "桜井", "野村", "高木", "菊池", "大野", "工藤",
    "水野", "吉川", "山内", "西田", "土屋", "東", "吉村", "小島", "大西", "大久保"
]

# 男性の名
FIRST_NAMES_M = [
    "翔太", "大輔", "健太", "達也", "直樹", "拓也", "和也", "亮太", "剛", "誠",
    "大樹", "翼", "一輝", "哲也", "淳", "健", "大介", "隆", "将太", "翔",
    "蓮", "湊", "陽翔", "樹", "悠真", "大翔", "朝陽", "碧", "陸", "海斗",
    "一郎", "次郎", "三郎", "四郎", "五郎", "正一", "健二", "信三", "光司", "吾郎",
    "秀吉", "家康", "信長", "政宗", "幸村", "義経", "弁慶", "尊氏", "直義", "師直",
    "浩", "博", "茂", "実", "隆", "清", "進", "勝", "修", "明"
]

# 女性の名
FIRST_NAMES_F = [
    "陽菜", "美咲", "愛", "未来", "彩", "遥", "優花", "七海", "花音", "結衣",
    "葵", "美月", "莉子", "凛", "結菜", "美桜", "陽葵", "芽依", "紬", "咲良",
    "花子", "梅子", "桜", "桃子", "百合", "薫", "直美", "久美子", "裕子", "真由美",
    "恵", "智子", "理恵", "由美", "美香", "加奈", "沙織", "亜美", "絵里", "麻衣",
    "京子", "洋子", "幸子", "悦子", "順子", "典子", "美代子", "和子", "明子", "優子"
]

# 自動車メーカー名のパーツ (英語風)
MAKER_PREFIXES = [
    "Apex", "Vertex", "Omega", "Titan", "Nova", "Zenith", "Prime", "Global", "Future", "Dynamic",
    "Royal", "Grand", "Rapid", "Swift", "Aero", "Techno", "Cyber", "Eco", "Solar", "Lunar",
    "Aurora", "Borealis", "Cosmos", "Dimension", "Eclipse", "Frontier", "Galaxy", "Horizon", "Infinity", "Jupiter"
]
MAKER_SUFFIXES = [
    "Motors", "Automotive", "Industries", "Auto", "Cars", "Mobility", "Engineering", "Works", "Factory", "Lab",
    "Systems", "Technologies", "Group", "Holdings", "Corporation", "Inc.", "Ltd.", "Co."
]

# 小売店名のパーツ (日本語風)
RETAIL_PREFIXES = [
    "オート", "カーライフ", "ガレージ", "カーショップ", "モータース", "カープラザ", "オートサロン", "ドライブ", "マイカー", "ファミリー",
    "スマイル", "ハッピー", "ドリーム", "サンシャイン", "レインボー", "スター", "シティ", "タウン", "ワールド", "ベスト"
]
RETAIL_SUFFIXES = [
    "センター", "ステーション", "ワールド", "ランド", "市場", "館", "屋", "本舗", "販売", "ディーラー",
    "商会", "オート", "ガレージ", "ショップ", "広場", "ガーデン", "パーク", "アベニュー"
]

# 車種名のパーツ (形容詞 + 名詞, あるいは単語)
PRODUCT_NAMES_A = [
    "Falcon", "Eagle", "Hawk", "Wolf", "Tiger", "Lion", "Bear", "Shark", "Dolphin", "Whale",
    "Storm", "Thunder", "Lightning", "Wind", "Breeze", "Cloud", "Sky", "Star", "Moon", "Sun",
    "Alpha", "Beta", "Gamma", "Delta", "Sigma", "Omega", "Zeta", "Theta", "Iota", "Kappa",
    "Grand", "Super", "Ultra", "Hyper", "Mega", "Giga", "Tera", "Neo", "Pro", "Max",
    "Phantom", "Ghost", "Spirit", "Soul", "Shadow", "Light", "Ray", "Beam", "Spark", "Flame"
]
PRODUCT_NAMES_B = [
    "Cruiser", "Runner", "Sprinter", "Walker", "Driver", "Rider", "Pilot", "Navigator", "Explorer", "Voyager",
    "Sedan", "Coupe", "Wagon", "Van", "Truck", "Lorry", "Bus", "Coach", "Roadster", "Spider",
    "GT", "RS", "SS", "GTS", "GTR", "X", "S", "L", "Z", "R",
    "Cross", "Sport", "Touring", "Limited", "Custom", "Special", "Edition", "Prime", "Elite", "Master"
]

# 施設名
BUILDING_NAMES = [
    "サンシャインビル", "グランドタワー", "セントラルビル", "パークサイドビル", "メトロポリス",
    "オーシャンビュー", "ヒルサイドテラス", "スカイタワー", "リバーサイド", "シティセンター",
    "丸の内ビル", "大手町タワー", "新宿センタービル", "渋谷スクエア", "六本木ヒルズ", "品川インターシティ", "横浜ランドマーク",
    "ミッドタウン", "ゲートウェイタワー", "アークヒルズ", "ガーデンプレイス", "フロントタワー", "ワールドトレードセンター",
    "パシフィックセンチュリー", "イーストタワー", "ウエストタワー", "サウスタワー", "ノースタワー",
    "国際ビル", "平和ビル", "未来ビル", "テクノロジーパーク", "イノベーションセンター"
]

# サプライヤー名のパーツ
SUPPLIER_PREFIXES = [
    "日本", "東京", "帝国", "東洋", "国際", "大和", "富士", "三河", "日立", "三菱", 
    "住友", "安田", "古河", "川崎", "昭和", "平成", "アジア", "ワールド", "ユニバーサル",
    "ジャパン", "グローバル", "フューチャー", "アドバンス", "ハイテク", "ニッポン", "オリエンタル",
    "セントラル", "パシフィック", "ロイヤル", "ダイナミック", "スーパー", "ワンダー", "スター"
]
SUPPLIER_SUFFIXES = [
    "製作所", "工業", "精機", "テクノロジー", "システムズ", "技研", "重工", 
    "エンジニアリング", "ワークス", "インダストリー", "サプライ", "ソリューションズ",
    "マニュファクチャリング", "クリエイション", "ラボ", "研究所", "本店", "商会", "ファクトリー", "産業"
]

def generate_person_name(gender):
    last = random.choice(LAST_NAMES)
    first = random.choice(FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F)
    return f"{last} {first}"

def generate_company_name(type_):
    if type_ == 'npc_maker':
        # 英語風のかっこいい名前
        prefix = random.choice(MAKER_PREFIXES)
        suffix = random.choice(MAKER_SUFFIXES)
        return f"{prefix} {suffix}"
    elif type_ == 'npc_retail':
        # 日本語風の親しみやすい名前
        prefix = random.choice(RETAIL_PREFIXES)
        suffix = random.choice(RETAIL_SUFFIXES)
        return f"{prefix}{suffix}"
    else:
        return f"Company {random.randint(100, 999)}"

def generate_product_name(strategy=None):
    # 戦略に応じて傾向を変えることも可能だが、まずはランダム
    part_a = random.choice(PRODUCT_NAMES_A)
    
    # 40%の確率で単語のみ、60%で2単語
    if random.random() < 0.4:
        return part_a
    else:
        part_b = random.choice(PRODUCT_NAMES_B)
        return f"{part_a} {part_b}"

def generate_facility_name(type_):
    if type_ == 'office':
        name = random.choice(BUILDING_NAMES)
        floor = random.randint(1, 40)
        return f"{name} {floor}F"
    elif type_ == 'factory':
        names = [
            "第一工場", "第二工場", "中央工場", "臨海工場", "テクニカルセンター", "マザー工場", "東部工場", "西部工場", "埼玉工場", "神奈川工場",
            "千葉製造所", "群馬製作所", "栃木工場", "静岡事業所", "愛知工場", "大阪製造部", "九州工場", "北海道工場",
            "アドバンスド・マニュファクチャリング・センター", "グローバル生産センター", "試作開発センター", "部品センター"
        ]
        return random.choice(names)
    elif type_ == 'store':
        names = [
            "本店", "駅前店", "中央通り店", "バイパス店", "港店", "南店", "北店", "ショッピングモール店", "銀座店", "表参道店",
            "新宿店", "渋谷店", "池袋店", "横浜店", "梅田店", "難波店", "博多店", "札幌店", "仙台店", "名古屋店",
            "アウトレットパーク店", "メガストア", "フラッグシップストア", "サテライトショップ", "ショールーム"
        ]
        return random.choice(names)
    return "未設定"

def generate_supplier_name(part_label=""):
    prefix = random.choice(SUPPLIER_PREFIXES)
    suffix = random.choice(SUPPLIER_SUFFIXES)
    return f"{prefix}{suffix}"
