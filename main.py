import os
import json
import sqlite3
import redis
import shutil
import random
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 1. 挂载与路径
IMAGES_DIR = "/app/data/images"
os.makedirs(IMAGES_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "kiosk.db")
USER_DATA_DIR = os.path.join(DATA_DIR, "user_data")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)

# 2. Redis
redis_host = os.getenv("REDIS_HOST", "localhost")
r = redis.Redis(host=redis_host, port=6379, decode_responses=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (email TEXT PRIMARY KEY, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  email TEXT, game_title TEXT, image_url TEXT, claim_time TEXT)''')
    conn.commit()
    conn.close()
init_db()

# Models
class Account(BaseModel):
    email: str
    password: str

class NukeRequest(BaseModel):
    email: str

class QueryAccount(BaseModel):
    email: str 

class GameLog(BaseModel):
    email: str
    game_title: str
    image_filename: str

# --- 🛡️ 防滥用中间件 (新增) ---
@app.middleware("http")
async def anti_abuse_middleware(request: Request, call_next):
    # 仅针对“提交任务/启动引擎”接口进行限制
    if request.url.path == "/api/deposit" and request.method == "POST":
        client_ip = request.client.host
        
        # 1. 检查是否已被永久封禁
        if r.exists(f"ban:{client_ip}"):
            return JSONResponse(status_code=403, content={"status": "banned", "msg": "🚫 此 IP 已因滥用被永久封禁，请联系管理员。"})
        
        # 2. 频率计数 (Key: rate:IP, 有效期: 1小时)
        limit_key = f"rate:{client_ip}"
        current_count = r.incr(limit_key)
        
        # 如果是第一次请求，设置 1 小时过期时间
        if current_count == 1:
            r.expire(limit_key, 3600)
        
        # 3. 超过 5 次，执行永久封禁
        if current_count > 5:
            r.set(f"ban:{client_ip}", "1") # 永久Key，不设过期时间
            return JSONResponse(status_code=403, content={"status": "banned", "msg": "🚫 操作过于频繁(>5次/小时)，IP 已被永久封禁。"})

    response = await call_next(request)
    return response

# --- 🛠️ 内部工具函数：物理删除逻辑 ---
def _perform_physical_delete(email):
    """执行彻底删除操作：数据库 + 物理文件夹 + Redis缓存"""
    log_msgs = []
    
    # 1. 删数据库
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE email=?", (email,))
    if c.rowcount > 0:
        log_msgs.append("数据库记录已删")
    conn.commit()
    conn.close()

    # 2. 删物理文件
    target_dir = os.path.join(USER_DATA_DIR, email)
    if os.path.exists(target_dir):
        try:
            shutil.rmtree(target_dir)
            log_msgs.append("物理文件夹已粉碎")
        except Exception as e:
            log_msgs.append(f"物理删除出错: {e}")
    
    # 3. 删 Redis
    r.delete(f"status:{email}")
    r.delete(f"result:{email}")
    r.delete(f"last_game:{email}")
    r.delete(f"pending_game:{email}")
    
    return "，".join(log_msgs)

# --- API 接口 ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/deposit")
async def deposit(account: Account):
    task = {"email": account.email, "password": account.password, "mode": "verify"}
    r.delete(f"status:{account.email}")
    r.delete(f"result:{account.email}")
    r.rpush("task_queue", json.dumps(task))
    return {"status": "queued", "msg": "正在加入队列..."}

@app.post("/api/delete_account")
async def delete_account(account: Account):
    """用户手动删除接口（需要验证密码）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password FROM accounts WHERE email=?", (account.email,))
    row = c.fetchone()
    conn.close()
    
    if row and row[0] != account.password:
        return {"status": "fail", "msg": "密码错误，无法删除"}
    
    msg = _perform_physical_delete(account.email)
    return {"status": "success", "msg": f"手动删除成功: {msg}"}

# Worker 专用的核弹接口（无需密码，直接销毁）
@app.post("/api/nuke_account")
async def nuke_account(req: NukeRequest):
    print(f"☢️ 接到 Worker 指令，正在销毁无效账号: {req.email}")
    msg = _perform_physical_delete(req.email)
    return {"status": "success", "msg": msg}

@app.get("/api/status/{email}")
async def get_status(email: str):
    status_msg = r.get(f"status:{email}")
    result = r.get(f"result:{email}")
    last_game = r.get(f"last_game:{email}") 
    if not status_msg: return {"status": "waiting", "msg": "Waiting..."}
    return {"status": "processing", "msg": status_msg, "result": result, "game_title": last_game}

@app.post("/api/confirm_success")
async def save_account(account: Account):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO accounts (email, password) VALUES (?, ?)", (account.email, account.password))
    conn.commit()
    conn.close()
    return {"status": "saved"}

@app.post("/api/query")
async def query_logs(account: QueryAccount):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT game_title, claim_time, image_url FROM logs WHERE email=? ORDER BY id DESC", (account.email,))
    rows = c.fetchall()
    conn.close()
    logs = [{"game": r[0], "time": r[1], "image": f"/images/{r[2]}" if r[2] else "/images/default.jpg"} for r in rows]
    return {"status": "success", "data": logs}

@app.post("/api/report_game")
async def report_game(log: GameLog):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM logs WHERE email=? AND game_title=?", (log.email, log.game_title))
    if c.fetchone():
        conn.close()
        return {"status": "skipped", "msg": "Already recorded"}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    r.set(f"last_game:{log.email}", log.game_title, ex=600)
    c.execute("INSERT INTO logs (email, game_title, image_url, claim_time) VALUES (?, ?, ?, ?)",
              (log.email, log.game_title, log.image_filename, now))
    conn.commit()
    conn.close()
    return {"status": "recorded"}

# --- 🚦 错峰调度逻辑 (新增) ---

def push_task_to_redis(task_json):
    """这才是真正把任务推进队列的函数，由调度器触发"""
    task_data = json.loads(task_json)
    r.rpush("task_queue", task_json)
    print(f"🚦 [错峰执行] 任务已入队: {task_data['email']}")

def daily_job():
    print("⏰ 12点已到，正在为所有账号计算随机延迟...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT email, password FROM accounts")
    users = cursor.fetchall()
    conn.close()
    
    for email, password in users:
        task = {"email": email, "password": password, "mode": "claim"}
        task_json = json.dumps(task)
        
        # 🎲 生成 0 到 60 分钟 (3600秒) 的随机延迟
        jitter_seconds = random.randint(0, 3600)
        run_date = datetime.now() + timedelta(seconds=jitter_seconds)
        
        # 使用 APScheduler 的 'date' 触发器，在指定时间执行一次
        scheduler.add_job(push_task_to_redis, 'date', run_date=run_date, args=[task_json])
        
        print(f"📅 账号 {email} 将延迟 {jitter_seconds/60:.1f} 分钟，于 {run_date.strftime('%H:%M:%S')} 执行")

scheduler = AsyncIOScheduler()
scheduler.add_job(daily_job, 'cron', hour=12, minute=0)
scheduler.start()