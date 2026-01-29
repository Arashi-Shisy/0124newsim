# c:\0124newSIm\gamebalance.py
# ゲームバランスに関わる定数を一括管理するファイル

# 時間・年齢関連
WEEKS_PER_AGE = 13  # 13週で1歳年を取る
NPC_SCALE_FACTOR = 8 # NPC1人あたりの人数換算 (10人分の働きとコスト)
START_AGE = 22
RETIREMENT_AGE = 66
LIFESPAN_WEEKS = (RETIREMENT_AGE - START_AGE) * WEEKS_PER_AGE

# 経済・市場関連
BASE_MARKET_DEMAND = 800  # 自動車業界の週次基礎需要（台） - 供給過多にするため引き下げ
ECONOMIC_INDEX_MIN = 0.8
ECONOMIC_INDEX_MAX = 1.2
RANDOM_FLUCTUATION = 0.05

# 企業・財務関連
INITIAL_FUNDS_MAKER = 5000000000  # 50億円
INITIAL_FUNDS_RETAIL = 5000000000   # 50億円
MAKER_UNIT_SALES_PRICE = 2700000   # メーカー -> 小売
RETAIL_UNIT_SALES_PRICE_BASE = 3000000 # 小売 -> 顧客（基準）

# 自動車業界コスト構造 (1台あたり)
INDUSTRIES = {
    "automotive": {
        "name": "自動車",
        "parts": [
            {"key": "engine", "label": "エンジン", "base_cost": 240000},
            {"key": "drive_parts", "label": "走行パーツ", "base_cost": 240000},
            {"key": "suspension", "label": "足回り", "base_cost": 180000},
            {"key": "safety", "label": "安全機器", "base_cost": 72000},
            {"key": "auxiliary", "label": "補機類", "base_cost": 48000},
            {"key": "body", "label": "車体", "base_cost": 240000},
            {"key": "interior", "label": "インテリア", "base_cost": 180000}
        ]
    }
}
CURRENT_INDUSTRY = "automotive"
TOTAL_MATERIAL_COST = sum(p['base_cost'] for p in INDUSTRIES[CURRENT_INDUSTRY]['parts'])

# 施設・賃料 (週次)
RENT_OFFICE = 35000  # 1人あたり
RENT_FACTORY = 25000 # 1人あたり
RENT_STORE_BASE = 30000 # 1人あたり(Access B)
FACILITY_UNIT_SIZE = 5 # 施設を借りる際の最小単位（人）
FACILITY_PURCHASE_MULTIPLIER = 100 # 購入価格は週次賃料の100倍

# 生産・業務効率
BASE_PRODUCTION_EFFICIENCY = 0.2 # 台/NPC/週
BASE_SALES_EFFICIENCY = 0.7 # 台/NPC/週 (店舗販売)
HR_CAPACITY_PER_PERSON = 6 # 人事1人で管理できる人数

# NPC能力関連
ABILITY_MIN = 0
ABILITY_MAX = 100
GENIUS_RATE = 0.05
GROWTH_RATE_NORMAL = 0.05
GROWTH_RATE_HIGH = 0.1
GROWTH_RATE_LOW = 0.025
INDUSTRY_APTITUDE_MAX = 2.0
INDUSTRY_APTITUDE_GROWTH_FAST = 1.0 / 13 # 13週で1.0へ

# 給与基準
BASE_SALARY_YEARLY = 4000000 # 能力50の時
WEEKS_PER_YEAR_REAL = 52 # 給与計算用（ゲーム内進行とは別）
REHIRE_PROHIBITION_WEEKS = 52 # 離職後、元の会社に戻れない期間

# 部署定義
DEPT_PRODUCTION = "production"
DEPT_SALES = "sales"
DEPT_DEV = "development"
DEPT_HR = "hr"
DEPT_PR = "pr"
DEPT_ACCOUNTING = "accounting"
DEPT_STORE = "store" # 店舗配属

DEPARTMENTS = [
    DEPT_PRODUCTION, DEPT_SALES, DEPT_DEV, DEPT_HR, DEPT_PR, DEPT_ACCOUNTING, DEPT_STORE
]

# 役職
ROLE_MEMBER = "member"
ROLE_ASSISTANT_MANAGER = "assistant_manager"
ROLE_MANAGER = "manager"
ROLE_CXO = "cxo"
ROLE_CEO = "ceo"

# マネジメントボーナス係数 (部下の能力平均に加算される係数)
MGMT_BONUS_MANAGER = 0.1
MGMT_BONUS_CXO = 0.25

# 開発関連
DEVELOPMENT_DURATION = 26 # 開発にかかる週数 (半年)
DEV_KNOWHOW_GAIN = 0.5 # 開発完了時に得られるノウハウ
DEV_KNOWHOW_EFFECT = 0.05 # ノウハウ1ポイントあたりのコンセプトスコアへのボーナス
CONCEPT_DECAY_RATE = 0.999 # 週次のコンセプト陳腐化率 (1 - 0.001)

# 開発方針
DEV_STRATEGY_CONCEPT_SPECIALIZED = "concept_specialized"
DEV_STRATEGY_CONCEPT_FOCUSED = "concept_focused"
DEV_STRATEGY_BALANCED = "balanced"
DEV_STRATEGY_EFFICIENCY_FOCUSED = "efficiency_focused"
DEV_STRATEGY_EFFICIENCY_SPECIALIZED = "efficiency_specialized"

DEV_STRATEGIES = {
    DEV_STRATEGY_CONCEPT_SPECIALIZED: {"name": "コンセプト特化", "c_mod": 1.5, "e_mod": 0.6},
    DEV_STRATEGY_CONCEPT_FOCUSED: {"name": "コンセプト重視", "c_mod": 1.2, "e_mod": 0.8},
    DEV_STRATEGY_BALANCED: {"name": "バランス重視", "c_mod": 1.0, "e_mod": 1.0},
    DEV_STRATEGY_EFFICIENCY_FOCUSED: {"name": "生産効率重視", "c_mod": 0.8, "e_mod": 1.2},
    DEV_STRATEGY_EFFICIENCY_SPECIALIZED: {"name": "生産効率特化", "c_mod": 0.6, "e_mod": 1.5},
}

# 銀行・融資
BASE_CREDIT_RATING = 50
INTEREST_RATE_MIN = 0.01 # 年利1%
INTEREST_RATE_MAX = 0.15 # 年利15%
LOAN_TERM_WEEKS = 52     # 返済期間（週）
CREDIT_LIMIT_MULTIPLIER = 10000000 # 格付け1あたり1000万円の枠

# 広告
AD_COST_UNIT = 1000000 # 1単位100万円
AD_EFFECT_BASE = 1.0 # 1単位あたりの上昇ベース値
BRAND_DECAY_BASE = 0.90 # ブランド力の基本減衰率 (広報0の場合、毎週10%減)
AWARENESS_DECAY_BASE = 0.85 # 商品認知度の基本減衰率 (広報0の場合、毎週15%減)
PR_MITIGATION_FACTOR = 0.001 # 広報力1につき減衰率を0.1%緩和

# 価格戦略
PRICE_ADJUST_RATE = 0.05 # 価格改定幅 (5%)
MIN_PROFIT_MARGIN = 1.1 # 最低利益率 (原価の1.1倍)

