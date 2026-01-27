# c:\0124newSIm\src\app.py
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import json
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

@app.template_filter('ability_range')
def ability_range_filter(value, hr_power):
    """能力値を人事能力に応じた範囲表示文字列に変換する"""
    if value is None: return "-"
    # 誤差範囲: 人事力0で40(±20), 人事力100で4(±2)
    # 0-100の範囲に収める
    width = 40 - (36 * (min(100, max(0, hr_power)) / 100.0))
    half_width = width / 2.0
    
    low = max(0, int(value - half_width))
    high = min(100, int(value + half_width))
    
    return f"{low}-{high}"

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
    
    return render_template('hr.html', employees=employees, candidates=candidates, departments=departments, hr_power=caps['hr'])

@app.route('/hr/change_dept', methods=['POST'])
def hr_change_dept():
    npc_id = request.form.get('npc_id')
    new_dept = request.form.get('new_dept')
    new_role = request.form.get('new_role')
    
    if npc_id and new_dept:
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
    effective_staff = min(prod_staff_count, total_factory_size)
    
    max_capacity = int(effective_staff * efficiency)
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
    
    return render_template('sales.html', pending_orders=pending_orders, history=history)

@app.route('/sales/action', methods=['POST'])
def sales_action():
    order_id = request.form.get('order_id')
    action = request.form.get('action') # accept or reject
    
    if order_id and action:
        status = 'accepted' if action == 'accept' else 'rejected'
        db.execute_query("UPDATE b2b_orders SET status = ? WHERE id = ?", (status, order_id))
        flash(f"注文を{'受注' if status=='accepted' else '拒否'}しました。", "info")
        
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
