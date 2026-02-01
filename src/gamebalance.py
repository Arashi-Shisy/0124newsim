# c:\0124newSIm\src\gamebalance.py
# ゲームバランスに関わる定数を一括管理するファイル

# 時間・年齢関連
WEEKS_PER_AGE = 13  # 13週で1歳年を取る
NPC_SCALE_FACTOR = 8 # NPC1人あたりの人数換算 (10人分の働きとコスト)
START_AGE = 22
RETIREMENT_AGE = 66
LIFESPAN_WEEKS = (RETIREMENT_AGE - START_AGE) * WEEKS_PER_AGE

# 経済・市場関連
BASE_MARKET_DEMAND = 1000  # 自動車業界の週次基礎需要（台） - 供給過多にするため引き下げ
ECONOMIC_INDEX_MIN = 0.8
ECONOMIC_INDEX_MAX = 1.2
RANDOM_FLUCTUATION = 0.05

# 企業・財務関連
INITIAL_FUNDS_MAKER = 1000000000  # 50億円
INITIAL_FUNDS_RETAIL = 1000000000   # 50億円
MAKER_UNIT_SALES_PRICE = 2700000   # メーカー -> 小売
RETAIL_UNIT_SALES_PRICE_BASE = 3000000 # 小売 -> 顧客（基準）

# 業界コスト構造 (1台あたり)
INDUSTRIES = {
    "automotive": {
        "name": "自動車業界",
        "categories": {
            "sedan": {
                "name": "セダン",
                "base_demand": 600, # 週次需要
                "development_duration": 48, # 開発期間(週): 長め
                "parts": [
                    {"key": "engine", "label": "エンジン", "base_cost": 240000},
                    {"key": "drive_parts", "label": "走行パーツ", "base_cost": 240000},
                    {"key": "suspension", "label": "足回り", "base_cost": 180000},
                    {"key": "safety", "label": "安全機器", "base_cost": 72000},
                    {"key": "auxiliary", "label": "補機類", "base_cost": 48000},
                    {"key": "body", "label": "車体", "base_cost": 240000},
                    {"key": "interior", "label": "インテリア", "base_cost": 180000}
                ]
            },
            "suv": {
                "name": "SUV",
                "base_demand": 1200,
                "development_duration": 52,
                "parts": [
                    {"key": "engine_high_power", "label": "高出力エンジン", "base_cost": 350000},
                    {"key": "drive_parts_awd", "label": "4WD走行パーツ", "base_cost": 300000},
                    {"key": "suspension_heavy", "label": "強化足回り", "base_cost": 220000},
                    {"key": "safety", "label": "安全機器", "base_cost": 80000},
                    {"key": "auxiliary", "label": "補機類", "base_cost": 55000},
                    {"key": "body_large", "label": "大型車体", "base_cost": 350000},
                    {"key": "interior", "label": "インテリア", "base_cost": 200000}
                ]
            },
            "compact": {
                "name": "コンパクトカー",
                "base_demand": 1500,
                "development_duration": 40,
                "parts": [
                    {"key": "engine_small", "label": "小型エンジン", "base_cost": 150000},
                    {"key": "drive_parts", "label": "走行パーツ", "base_cost": 180000},
                    {"key": "suspension", "label": "足回り", "base_cost": 120000},
                    {"key": "safety", "label": "安全機器", "base_cost": 60000},
                    {"key": "auxiliary", "label": "補機類", "base_cost": 40000},
                    {"key": "body_small", "label": "小型車体", "base_cost": 150000},
                    {"key": "interior_simple", "label": "簡易インテリア", "base_cost": 100000}
                ]
            },
            "sports": {
                "name": "スポーツカー",
                "base_demand": 200,
                "development_duration": 60,
                "parts": [
                    {"key": "engine_sport", "label": "スポーツエンジン", "base_cost": 500000},
                    {"key": "drive_parts_sport", "label": "スポーツ走行パーツ", "base_cost": 400000},
                    {"key": "suspension_sport", "label": "スポーツ足回り", "base_cost": 300000},
                    {"key": "safety", "label": "安全機器", "base_cost": 80000},
                    {"key": "auxiliary", "label": "補機類", "base_cost": 60000},
                    {"key": "body_aero", "label": "エアロ車体", "base_cost": 400000},
                    {"key": "interior_sport", "label": "スポーツインテリア", "base_cost": 250000}
                ]
            }
        }
    },
    "home_appliances": { # 新規追加
        "name": "家電業界",
        "categories": {
            # 家電業界全体で自動車の約10倍の需要(10,000台)になるようカテゴリで配分
            "washing_machine": {
                "name": "洗濯機",
                "base_demand": 3000,
                "development_duration": 13, # 要件: 3ヶ月 (13週)
                "parts": [
                    {"key": "motor", "label": "モーター", "base_cost": 5000},
                    {"key": "casing", "label": "外装", "base_cost": 3000},
                    {"key": "control_panel", "label": "操作パネル", "base_cost": 2000}
                ]
            },
            "refrigerator": {
                "name": "冷蔵庫",
                "base_demand": 4000,
                "development_duration": 16,
                "parts": [
                    {"key": "compressor", "label": "コンプレッサー", "base_cost": 8000},
                    {"key": "casing_large", "label": "大型外装", "base_cost": 6000},
                    {"key": "insulation", "label": "断熱材", "base_cost": 3000},
                    {"key": "control_panel", "label": "操作パネル", "base_cost": 2000}
                ]
            },
            "tv": {
                "name": "テレビ",
                "base_demand": 5000,
                "development_duration": 12,
                "parts": [
                    {"key": "display_panel", "label": "液晶パネル", "base_cost": 15000},
                    {"key": "circuit_board", "label": "基板", "base_cost": 5000},
                    {"key": "casing_thin", "label": "薄型外装", "base_cost": 2000},
                    {"key": "speakers", "label": "スピーカー", "base_cost": 1000}
                ]
            },
            "ac": {
                "name": "エアコン",
                "base_demand": 2500,
                "development_duration": 14,
                "parts": [
                    {"key": "compressor", "label": "コンプレッサー", "base_cost": 7000},
                    {"key": "heat_exchanger", "label": "熱交換器", "base_cost": 4000},
                    {"key": "fan", "label": "ファン", "base_cost": 1000},
                    {"key": "casing", "label": "外装", "base_cost": 2000}
                ]
            },
            "microwave": {
                "name": "電子レンジ",
                "base_demand": 3500,
                "development_duration": 10,
                "parts": [
                    {"key": "magnetron", "label": "マグネトロン", "base_cost": 3000},
                    {"key": "casing", "label": "外装", "base_cost": 2000},
                    {"key": "control_panel", "label": "操作パネル", "base_cost": 1500},
                    {"key": "turntable", "label": "ターンテーブル", "base_cost": 500}
                ]
            }
        }
    }
}

# デフォルト設定（後方互換用）
DEFAULT_INDUSTRY = "automotive"
DEFAULT_CATEGORY = "sedan"

# 施設・賃料 (週次)
RENT_OFFICE = 20000  # 1人あたり
RENT_FACTORY = 12000 # 1人あたり
RENT_STORE_BASE = 30000 # 1人あたり(Access B)
FACILITY_UNIT_SIZE = 8 # 施設を借りる際の最小単位（人）
FACILITY_PURCHASE_MULTIPLIER = 100 # 購入価格は週次賃料の100倍

# 生産・業務効率
BASE_PRODUCTION_EFFICIENCY = 0.27 # 台/NPC/週
BASE_SALES_EFFICIENCY = 2 # 台/NPC/週 (店舗販売)
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
DEV_KNOWHOW_GAIN = 0.5 # 開発完了時に得られるノウハウ
DEV_KNOWHOW_EFFECT = 0.05 # ノウハウ1ポイントあたりのコンセプトスコアへのボーナス
CONCEPT_DECAY_RATE = 0.999 # 週次のコンセプト陳腐化率 (1 - 0.001)
# 追加: 開発キャパシティ要件
# プロジェクト1つにつきキャパ2000が必要 (能力50のNPC 5人分)
REQ_CAPACITY_DEV_PROJECT = 2000

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
# 追加: 広報キャパシティ要件
# ブランド力+認知度合計 1ポイントにつきキャパ2.0が必要 (合計1000ポイントならキャパ2000=5人分必要)
REQ_CAPACITY_PR_POINT = 2.0

# 価格戦略
PRICE_ADJUST_RATE = 0.05 # 価格改定幅 (5%)
MIN_PROFIT_MARGIN = 1.1 # 最低利益率 (原価の1.1倍)

# 株式・株価
INITIAL_STOCK_PRICE = 50000
INITIAL_SHARES = 20000 # 10億円 / 5万円
PER_BASE = 15.0
PBR_BASE = 1.0
STOCK_VOLATILITY = 0.03 # 週次変動幅 (3%)

# IPO (新規上場)
IPO_MIN_NET_ASSETS = 1000000000 # 純資産10億円
IPO_MIN_PROFIT_WEEKS = 4 # 黒字継続週数 (直近4週累計)
IPO_MIN_CREDIT_RATING = 70 # 格付け70以上
IPO_NEW_SHARE_RATIO = 0.2 # 公募増資比率 (発行済の20%)
IPO_DISCOUNT_RATE = 0.9 # 公募価格ディスカウント (理論価格の90%)
IPO_FEE_RATE = 0.05 # 上場手数料 (調達額の5%)

# 経理・決算
ACCOUNTING_LOAD_PER_TRANSACTION = 0.05 # 取引1件あたりの経理負荷
ACCOUNTING_LOAD_PER_EMPLOYEE = 0.5 # 従業員1人あたりの経理負荷
QUARTER_WEEKS = 13 # 四半期の長さ
REPORT_PUBLISH_DELAY_PENALTY = 0.1 # 決算遅延時の株価下落率

# 追加: 営業キャパシティ要件
# 取引1件につきキャパ20が必要 (能力50のNPC 1人で週20件さばける)
REQ_CAPACITY_SALES_TRANSACTION = 20
# 在庫1台につきキャパ0.1が必要 (在庫管理・棚卸負荷)
REQ_CAPACITY_SALES_STOCK = 0.1
