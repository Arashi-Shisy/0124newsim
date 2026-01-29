# c:\0124newSIm\src\app.py
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import json
import random
import sqlite3
from database import db
from simulation import Simulation
import gamebalance as gb

app = Flask(__name__)
app.secret_key = 'newsim_secret_key'

# シミュレーションインスタンス
sim = Simulation()

def get_player_company():
    """プレイヤー企業を取得する"""
    res = db.fetch_one("SELECT * FROM companies WHERE type = 'player' LIMIT 1")
    return res

@app.context_processor
def inject_common_data():
    """全テンプレートで共通して使えるデータを注入"""
    player = get_player_company()
    game_state = db.fetch_one("SELECT * FROM game_state")
    current_week = game_state['week'] if game_state else 0
    
    # 日付表示（週数から年月を簡易計算: 1月1週スタートと仮定）
    year = 2025 + (current_week - 1) // 52
    week_of_year = (current_week - 1) % 52 + 1
    month = (week_of_year - 1) // 4 + 1
    if month > 12: month = 12
    date_str = f"{year}年 {month}月 第{week_of_year % 4 + 1}週 (Week {current_week})"

    return dict(
        player=player,
        current_week=current_week,
        date_str=date_str,
        active_page=request.endpoint
    )

def get_ability_bounds(value, hr_power):
    """能力値と人事力から、表示範囲(low, high)を計算する共通関数"""
    # 誤差範囲: 人事力0で40(±20), 人事力100で4(±2)
    # 0-100の範囲に収める
    width = 40 - (36 * (min(100, max(0, hr_power)) / 100.0))
    
    # 週と値に基づいてシードを決定（週が変わると表示範囲も変わる＝再評価される）
    current_week = sim.get_current_week()
    seed = (current_week * 1000) + value
    rng = random.Random(seed)
    
    # 真の値が範囲内のどこに来るかをランダムに決定 (0.0 ~ 1.0)
    bias = rng.random()
    
    # 範囲の計算 (value = low + width * bias)
    low = value - (width * bias)
    high = low + width
    
    # 0-100の範囲に収める（幅を維持するようにスライド）
    if low < 0:
        low = 0
        high = width
    elif high > 100:
        high = 100
        low = 100 - width
        
    return max(0, int(low)), min(100, int(high))

@app.template_filter('ability_range')
def ability_range_filter(value, hr_power):
    """能力値を人事能力に応じた範囲表示文字列に変換する"""
    if value is None: return "-"
    low, high = get_ability_bounds(value, hr_power)
    return f"{low}-{high}"

@app.template_filter('perceived_value')
def perceived_value_filter(value, hr_power):
    """ソート用に、表示範囲の中央値（推定値）を返す"""
    if value is None: return 0
    low, high = get_ability_bounds(value, hr_power)
    
    return (low + high) / 2.0

@app.route('/')
def dashboard():
    player = get_player_company()
    if not player:
        return "Player company not found. Please run seed.py first."
    
    # ダッシュボード用データの取得
    # 1. 資金
    funds = player['funds']
    
    # 2. 従業員数
    emp_count = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs WHERE company_id = ?", (player['id'],))['cnt']
    
    # 3. アラート（簡易実装：資金不足やキャパシティ不足など）
    alerts = []
    if funds < 0:
        alerts.append("資金がマイナスです！倒産の危機です。")
    
    # 未承認のB2B注文チェック
    pending_orders = db.fetch_one("SELECT COUNT(*) as cnt FROM b2b_orders WHERE seller_id = ? AND status = 'pending'", (player['id'],))['cnt']
    if pending_orders > 0:
        alerts.append(f"未承認の注文が {pending_orders} 件あります。「営業」画面で確認してください。")
    
    # 4. ニュース（直近のログ）
    news = db.fetch_all("SELECT * FROM news_logs WHERE week = ? ORDER BY id DESC LIMIT 5", (sim.get_current_week() - 1,))
    
    return render_template('dashboard.html', funds=funds, emp_count=emp_count, alerts=alerts, news=news)

@app.route('/hr')
def hr():
    player = get_player_company()
    
    # 人事能力の取得（表示誤差計算用）
    caps = sim.calculate_capabilities(player['id'])
    
    # 従業員一覧
    employees = db.fetch_all("SELECT * FROM npcs WHERE company_id = ?", (player['id'],))
    
    # 候補者一覧（労働市場）
    candidates = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL LIMIT 50")
    
    # 部署リスト
    departments = gb.DEPARTMENTS

    # 部署別統計の集計
    dept_stats = {}
    for d in departments:
        count = len([e for e in employees if e['department'] == d])
        space = count * gb.NPC_SCALE_FACTOR
        dept_stats[d] = {'count': count, 'space': space}
    
    # 交渉中（オファー済み）の候補者取得
    offers = db.fetch_all("""
        SELECT j.*, n.name, n.age, n.desired_salary as current_desired, 
               n.diligence, n.adaptability, n.production, n.store_ops, n.sales, n.hr, n.development, n.pr, n.accounting, n.management
        FROM job_offers j
        JOIN npcs n ON j.npc_id = n.id
        WHERE j.company_id = ?
    """, (player['id'],))

    offered_npc_ids = [o['npc_id'] for o in offers]
    
    return render_template('hr.html', employees=employees, candidates=candidates, departments=departments, caps=caps, hr_power=caps['hr'], npc_scale=gb.NPC_SCALE_FACTOR, dept_stats=dept_stats, offers=offers, offered_npc_ids=offered_npc_ids)

@app.route('/hr/change_dept', methods=['POST'])
def hr_change_dept():
    npc_id = request.form.get('npc_id')
    new_dept = request.form.get('new_dept')
    new_role = request.form.get('new_role')
    
    player = get_player_company()

    if npc_id and new_dept:
        # 役職制限チェック (部長・部長補佐は各部署1人まで)
        if new_role in [gb.ROLE_MANAGER, gb.ROLE_ASSISTANT_MANAGER]:
            existing = db.fetch_one("""
                SELECT id, name FROM npcs 
                WHERE company_id = ? AND department = ? AND role = ? AND id != ?
            """, (player['id'], new_dept, new_role, npc_id))
            
            if existing:
                flash(f"{new_dept}には既に{existing['name']}が{new_role}として着任しています。各部署1名までです。", "error")
                return redirect(url_for('hr'))

        db.execute_query("UPDATE npcs SET department = ?, role = ? WHERE id = ?", (new_dept, new_role, npc_id))
        flash(f"人事異動を発令しました。", "success")
    
    return redirect(url_for('hr'))

@app.route('/hr/change_salary', methods=['POST'])
def hr_change_salary():
    npc_id = request.form.get('npc_id')
    new_salary = request.form.get('new_salary')
    
    if npc_id and new_salary:
        try:
            salary_int = int(new_salary)
            db.execute_query("UPDATE npcs SET salary = ? WHERE id = ?", (salary_int, npc_id))
            flash(f"給与改定を行いました。", "success")
        except ValueError:
            flash("給与には数値を入力してください。", "error")
            
    return redirect(url_for('hr'))

@app.route('/hr/fire', methods=['POST'])
def hr_fire():
    npc_id = request.form.get('npc_id')
    current_week = sim.get_current_week()
    player = get_player_company()
    
    if npc_id:
        db.execute_query("""
            UPDATE npcs SET company_id = NULL, department = NULL, role = NULL, 
            last_resigned_week = ?, last_company_id = ? 
            WHERE id = ?
        """, (current_week, player['id'], npc_id))
        flash("解雇しました。", "warning")
        
    return redirect(url_for('hr'))

@app.route('/hr/hire', methods=['POST'])
def hr_hire():
    npc_id = request.form.get('npc_id')
    offer_salary = request.form.get('offer_salary')
    target_dept = request.form.get('target_dept')
    current_week = sim.get_current_week()
    player = get_player_company()
    
    if npc_id and offer_salary:
        # オファー発行
        db.execute_query("""
            INSERT INTO job_offers (week, company_id, npc_id, offer_salary, target_dept)
            VALUES (?, ?, ?, ?, ?)
        """, (current_week, player['id'], npc_id, offer_salary, target_dept))
        flash("採用オファーを出しました。来週結果がわかります。", "info")
        
    return redirect(url_for('hr'))

@app.route('/next_week', methods=['POST'])
def next_week():
    new_week = sim.proceed_week()
    flash(f"第{new_week}週に進みました。", "info")
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/production')
def production():
    player = get_player_company()
    
    # 能力とキャパシティの計算
    caps = sim.calculate_capabilities(player['id'])
    
    # 完了済みの設計書（生産可能な製品）
    designs = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed'", (player['id'],))
    
    # 現在の在庫
    inventory = db.fetch_all("SELECT * FROM inventory WHERE company_id = ?", (player['id'],))
    inv_map = {i['design_id']: i['quantity'] for i in inventory}
    
    # 今週の生産済み数
    stats = db.fetch_one("SELECT production_ordered FROM weekly_stats WHERE week = ? AND company_id = ?", (sim.get_current_week(), player['id']))
    current_produced = stats['production_ordered'] if stats else 0
    
    # 工場キャパシティ（施設サイズ）
    facilities = db.fetch_all("SELECT size FROM facilities WHERE company_id = ? AND type = 'factory'", (player['id'],))
    total_factory_size = sum(f['size'] for f in facilities)
    
    # 生産効率 (能力値 / 50 * 基準効率)
    efficiency = (caps['production'] / 50.0) * gb.BASE_PRODUCTION_EFFICIENCY
    
    # 最大生産可能数 (人 * 効率) - 既に生産した分
    # 工場サイズ分の人数しか働けない
    prod_staff_count = len(db.fetch_all("SELECT id FROM npcs WHERE company_id = ? AND department = ?", (player['id'], gb.DEPT_PRODUCTION)))
    effective_staff_entities = min(prod_staff_count, int(total_factory_size // gb.NPC_SCALE_FACTOR))
    
    max_capacity = int(effective_staff_entities * gb.NPC_SCALE_FACTOR * efficiency)
    remaining_capacity = max(0, max_capacity - current_produced)

    return render_template('production.html', 
                           designs=designs, 
                           inv_map=inv_map, 
                           caps=caps, 
                           remaining_capacity=remaining_capacity,
                           max_capacity=max_capacity,
                           current_produced=current_produced)

@app.route('/production/order', methods=['POST'])
def production_order():
    player = get_player_company()
    design_id = request.form.get('design_id')
    quantity = int(request.form.get('quantity', 0))
    current_week = sim.get_current_week()
    
    if quantity > 0:
        # コスト計算
        design = db.fetch_one("SELECT * FROM product_designs WHERE id = ?", (design_id,))
        parts_config = json.loads(design['parts_config'])
        unit_cost = sum(p['cost'] for p in parts_config.values())
        total_cost = unit_cost * quantity
        
        if player['funds'] >= total_cost:
            # 資金消費
            db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (total_cost, player['id']))
            # 在庫追加
            existing = db.fetch_one("SELECT id FROM inventory WHERE company_id = ? AND design_id = ?", (player['id'], design_id))
            if existing:
                db.execute_query("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", (quantity, existing['id']))
            else:
                db.execute_query("INSERT INTO inventory (company_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?)", 
                                 (player['id'], design_id, quantity, design['sales_price']))
            
            # 統計更新
            db.increment_weekly_stat(current_week, player['id'], 'production_ordered', quantity)
            db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'material', ?)",
                             (current_week, player['id'], total_cost))
            
            flash(f"{design['name']} を {quantity}台 生産しました。", "success")
        else:
            flash("資金が不足しています。", "error")
            
    return redirect(url_for('production'))

@app.route('/store')
def store():
    player = get_player_company()
    caps = sim.calculate_capabilities(player['id'])
    
    # 店舗一覧
    stores = db.fetch_all("SELECT * FROM facilities WHERE company_id = ? AND type = 'store'", (player['id'],))
    
    # 在庫（店頭に並ぶ商品）
    inventory = db.fetch_all("""
        SELECT i.*, d.name as product_name, d.sales_price as msrp 
        FROM inventory i JOIN product_designs d ON i.design_id = d.id 
        WHERE i.company_id = ?
    """, (player['id'],))
    
    return render_template('store.html', stores=stores, inventory=inventory, caps=caps)

@app.route('/sales')
def sales():
    player = get_player_company()
    
    # 受注待ちの注文 (B2B)
    pending_orders = db.fetch_all("""
        SELECT o.*, c.name as buyer_name, d.name as product_name 
        FROM b2b_orders o 
        JOIN companies c ON o.buyer_id = c.id 
        JOIN product_designs d ON o.design_id = d.id
        WHERE o.seller_id = ? AND o.status = 'pending'
    """, (player['id'],))
    
    # 取引履歴
    history = db.fetch_all("""
        SELECT t.*, c.name as partner_name, d.name as product_name
        FROM transactions t
        LEFT JOIN companies c ON (t.buyer_id = c.id AND t.seller_id = ?) OR (t.seller_id = c.id AND t.buyer_id = ?)
        LEFT JOIN product_designs d ON t.design_id = d.id
        WHERE (t.seller_id = ? OR t.buyer_id = ?) AND t.type = 'b2b'
        ORDER BY t.id DESC LIMIT 20
    """, (player['id'], player['id'], player['id'], player['id']))
    
    # 仕入れ市場 (他社メーカーの在庫)
    market_stocks = db.fetch_all("""
        SELECT i.quantity, i.design_id, d.name as product_name, d.sales_price, d.concept_score, 
               i.company_id as seller_id, c.name as seller_name, c.brand_power
        FROM inventory i
        JOIN product_designs d ON i.design_id = d.id
        JOIN companies c ON i.company_id = c.id
        WHERE c.type IN ('npc_maker', 'player') AND c.id != ? AND c.is_active = 1 AND i.quantity > 0
    """, (player['id'],))
    
    return render_template('sales.html', pending_orders=pending_orders, history=history, market_stocks=market_stocks)

@app.route('/sales/action', methods=['POST'])
def sales_action():
    order_id = request.form.get('order_id')
    action = request.form.get('action') # accept or reject
    
    if order_id and action:
        status = 'accepted' if action == 'accept' else 'rejected'
        db.execute_query("UPDATE b2b_orders SET status = ? WHERE id = ?", (status, order_id))
        flash(f"注文を{'受注' if status=='accepted' else '拒否'}しました。", "info")
        
    return redirect(url_for('sales'))

@app.route('/sales/buy', methods=['POST'])
def sales_buy():
    player = get_player_company()
    seller_id = request.form.get('seller_id')
    design_id = request.form.get('design_id')
    quantity = int(request.form.get('quantity', 0))
    price = int(request.form.get('price', 0))
    current_week = sim.get_current_week()
    
    if quantity > 0:
        amount = quantity * price
        if player['funds'] >= amount:
            db.execute_query("""
                INSERT INTO b2b_orders (week, buyer_id, seller_id, design_id, quantity, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (current_week, player['id'], seller_id, design_id, quantity, amount))
            flash(f"発注を行いました (承認待ち): {quantity}台", "success")
        else:
            flash("資金が不足しています。", "error")
            
    return redirect(url_for('sales'))

@app.route('/dev')
def dev():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    # 開発中のプロジェクト
    developing = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'developing'", (player['id'],))
    
    # 完了済み
    completed = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed' ORDER BY id DESC", (player['id'],))
    
    # 新規開発用のパーツサプライヤー情報
    parts_data = gb.INDUSTRIES[gb.CURRENT_INDUSTRY]['parts']
    suppliers = {}
    for part in parts_data:
        sups = db.fetch_all("SELECT * FROM companies WHERE type = 'system_supplier' AND part_category = ?", (part['key'],))
        suppliers[part['key']] = sups
        
    strategies = gb.DEV_STRATEGIES
    
    return render_template('dev.html', developing=developing, completed=completed, 
                           parts_data=parts_data, suppliers=suppliers, strategies=strategies,
                           current_week=current_week, duration=gb.DEVELOPMENT_DURATION)

@app.route('/dev/start', methods=['POST'])
def dev_start():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    name = request.form.get('name')
    strategy = request.form.get('strategy')
    
    # パーツ構成の構築
    parts_config = {}
    total_score = 0
    parts_def = gb.INDUSTRIES[gb.CURRENT_INDUSTRY]['parts']
    
    for part in parts_def:
        sup_id = request.form.get(f"part_{part['key']}")
        if sup_id:
            sup = db.fetch_one("SELECT * FROM companies WHERE id = ?", (sup_id,))
            p_cost = int(part['base_cost'] * sup['trait_cost_multiplier'])
            parts_config[part['key']] = {
                "supplier_id": sup['id'],
                "score": sup['trait_material_score'],
                "cost": p_cost
            }
            total_score += sup['trait_material_score']
            
    avg_material_score = total_score / len(parts_def)
    
    db.execute_query("""
        INSERT INTO product_designs 
        (company_id, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, strategy, developed_week, parts_config)
        VALUES (?, ?, ?, 0, 0, 0, 0, 'developing', ?, ?, ?)
    """, (player['id'], name, avg_material_score, strategy, current_week, json.dumps(parts_config)))
    
    flash(f"新製品 {name} の開発を開始しました。", "success")
    return redirect(url_for('dev'))

@app.route('/pr')
def pr():
    player = get_player_company()
    products = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed'", (player['id'],))
    return render_template('pr.html', products=products)

@app.route('/facility')
def facility():
    player = get_player_company()
    
    # 保有/賃貸中の施設
    my_facilities = db.fetch_all("SELECT * FROM facilities WHERE company_id = ?", (player['id'],))
    
    # 市場の空き物件
    market_facilities = db.fetch_all("SELECT * FROM facilities WHERE company_id IS NULL LIMIT 50")
    
    return render_template('facility.html', my_facilities=my_facilities, market_facilities=market_facilities)

@app.route('/facility/contract', methods=['POST'])
def facility_contract():
    player = get_player_company()
    facility_id = request.form.get('facility_id')
    
    if facility_id:
        # 物件が空いているか確認
        fac = db.fetch_one("SELECT * FROM facilities WHERE id = ? AND company_id IS NULL", (facility_id,))
        if fac:
            # 契約処理 (賃貸)
            db.execute_query("UPDATE facilities SET company_id = ?, is_owned = 0 WHERE id = ?", (player['id'], facility_id))
            flash(f"{fac['name']} (賃料: ¥{fac['rent']:,}/週) を契約しました。", "success")
        else:
            flash("この物件は既に契約済みか、存在しません。", "error")
    
    return redirect(url_for('facility'))

@app.route('/world')
def world():
    # 市場トレンド
    trends = db.fetch_all("SELECT * FROM market_trends ORDER BY week DESC LIMIT 10")
    # 企業ランキング
    ranking = db.fetch_all("SELECT * FROM companies WHERE type != 'system_supplier' AND is_active = 1 ORDER BY funds DESC")
    
    return render_template('world.html', trends=trends, ranking=ranking)

if __name__ == '__main__':
    # データベースがない場合は初期化
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "newsim.db")):
        print("Database not found. Running seed...")
        from seed import run_seed
        run_seed()
        
    app.run(debug=True, port=5000)
