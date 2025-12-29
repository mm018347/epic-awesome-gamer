import os
import time
import json
import redis
import subprocess
import requests
import re
import shutil
import glob
from bs4 import BeautifulSoup

# Redis
redis_host = os.getenv("REDIS_HOST", "localhost")
r = redis.Redis(host=redis_host, port=6379, decode_responses=True)
WEB_BASE_URL = "http://web:8000"
WEB_API_URL = f"{WEB_BASE_URL}/api/report_game"
NUKE_API_URL = f"{WEB_BASE_URL}/api/nuke_account" # 核弹接口

IMAGES_DIR = "/app/data/images"
os.makedirs(IMAGES_DIR, exist_ok=True)

# 定义清理路径
PATHS_TO_CHECK = [
    "/app/data/user_data",          
    "/app/app/volumes/user_data"    
]

print("👷 Worker V26 (Delay Kill) 启动！")

def clean_filename(title):
    return re.sub(r'[\\/*?:"<>|]', "", title).replace(" ", "_").lower()

def clean_game_title_for_search(title):
    title = re.sub(r"(?i)\s+(goty|edition|director's cut|remastered|digital deluxe).*", "", title)
    return title.strip()

def fetch_steam_cover(game_title):
    search_title = clean_game_title_for_search(game_title)
    try:
        url = f"https://store.steampowered.com/api/storesearch/?term={search_title}&l=english&cc=US"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if data.get('total') > 0 and data.get('items'):
            app_id = data['items'][0]['id']
            return f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_600x900.jpg"
    except: pass
    return None

def scrape_and_download_image(game_title):
    print(f"🖼️ 刮削海报: 《{game_title}》")
    filename = f"{clean_filename(game_title)}.jpg"
    save_path = os.path.join(IMAGES_DIR, filename)
    if os.path.exists(save_path): return filename
    img_url = fetch_steam_cover(game_title)
    if not img_url:
        safe_name = game_title.replace(" ", "+")
        img_url = f"https://ui-avatars.com/api/?name={safe_name}&background=1e293b&color=3b82f6&size=512&length=2&font-size=0.33&bold=true"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        img_data = requests.get(img_url, headers=headers, timeout=10).content
        if len(img_data) > 1000:
            with open(save_path, 'wb') as f:
                f.write(img_data)
            return filename
    except: pass
    return None

def report_success(email, game_title):
    filename = scrape_and_download_image(game_title)
    try:
        requests.post(WEB_API_URL, json={
            "email": email, 
            "game_title": game_title,
            "image_filename": filename or "default.png"
        }, timeout=5)
        print(f"📡 尝试入库: {game_title}")
    except: pass

def clean_user_profile(email):
    """普通瘦身优化"""
    for base_dir in PATHS_TO_CHECK:
        profile_path = os.path.join(base_dir, email)
        if not os.path.exists(profile_path): continue
        
        folders_to_nuke = ["cache2", "startupCache", "thumbnails", "datareporting", "shader-cache", "crashes", "minidumps", "saved-telemetry-pings", "storage/default"]
        files_to_nuke = ["favicon*", "places.sqlite*", "formhistory.sqlite*", "webappsstore.sqlite*", "content-prefs.sqlite*", "*.log", "SiteSecurityServiceState.txt"]
        
        for folder in folders_to_nuke:
            try: shutil.rmtree(os.path.join(profile_path, folder))
            except: pass
        for pattern in files_to_nuke:
            for f in glob.glob(os.path.join(profile_path, pattern)):
                try: os.remove(f)
                except: pass

def nuke_account_immediately(email):
    """
    ☢️ 核弹模式：等待进程死亡后，执行双重删除
    """
    print(f"💀 [致命错误] 正在执行销毁程序: {email}")
    
    # ⚠️ 关键步骤：先睡 5 秒，让浏览器进程死透，防止它诈尸写回文件
    print("⏳ 等待浏览器进程完全退出 (5s)...")
    time.sleep(5)
    
    # 1. 呼叫后端删除 (后端权限通常更高)
    try:
        print(f"📞 呼叫后端 API: {NUKE_API_URL}")
        res = requests.post(NUKE_API_URL, json={"email": email}, timeout=5)
        print(f"📞 后端响应: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ 后端 API 连接失败: {e}")
    
    # 2. Worker 再次执行本地物理删除 (补刀)
    print("🗑️ 执行本地物理补刀...")
    for base_dir in PATHS_TO_CHECK:
        target_dir = os.path.join(base_dir, email)
        if os.path.exists(target_dir):
            try: 
                shutil.rmtree(target_dir)
                print(f"✅ [补刀成功] 已粉碎文件夹: {target_dir}")
            except Exception as e:
                print(f"❌ 删除失败 {target_dir}: {e}")
        else:
            print(f"ℹ️ 路径不存在(无需补刀): {target_dir}")

def run_task(task_data):
    email = task_data.get("email")
    password = task_data.get("password")
    mode = task_data.get("mode") 
    
    print(f"🚀 接到任务: {mode} - {email}")
    r.set(f"status:{email}", "🚀 初始化环境...", ex=3600)
    
    env = os.environ.copy()
    env["EPIC_EMAIL"] = email
    env["EPIC_PASSWORD"] = password
    env["ENABLE_APSCHEDULER"] = "false" 
    
    cmd = ["xvfb-run", "-a", "uv", "run", "app/deploy.py"]

    is_login_success = False
    has_critical_error = False
    is_fatal_failure = False
    is_already_owned = False

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
            env=env, text=True, bufsize=1
        )

        for line in process.stdout:
            line = line.strip()
            if not line: continue
            print(f"[{email}] {line}")

            # 🛑 致命错误 A: 无法获取 Cookie
            if "context cookies is not available" in line:
                r.set(f"status:{email}", "❌ 登录失败：无效账号，已自动销毁", ex=300)
                r.set(f"result:{email}", "fail", ex=3600)
                is_fatal_failure = True
                process.kill()
                nuke_account_immediately(email) 
                return

            # 🛑 致命错误 B: 密码错误
            if "invalid_account_credentials" in line:
                r.set(f"status:{email}", "❌ 密码错误：账号已自动销毁", ex=300)
                r.set(f"result:{email}", "fail", ex=3600)
                process.kill()
                nuke_account_immediately(email)
                return

            if "Could not find Place Order button" in line:
                r.set(f"status:{email}", "⚠️ 找不到下单按钮", ex=3600)
                has_critical_error = True
            
            if "Timeout 30000ms exceeded" in line:
                r.set(f"status:{email}", "⚠️ 网络超时，重试中...", ex=3600)
                has_critical_error = True

            if "Already in the library" in line:
                is_already_owned = True
                has_critical_error = False 
                r.set(f"status:{email}", "ℹ️ 游戏已在库中", ex=3600)

            if "Authentication completed" in line or "already logged in" in line:
                r.set(f"status:{email}", "✅ 登录流程结束", ex=3600)
                is_login_success = True

            if '"title":' in line:
                try:
                    match = re.search(r'"title":\s*"([^"]+)"', line)
                    if match:
                        game_name = match.group(1)
                        r.set(f"status:{email}", f"🎁 扫描到: {game_name}", ex=3600)
                        r.set(f"pending_game:{email}", game_name, ex=3600)
                        scrape_and_download_image(game_name)
                except: pass

            if "Free games collection completed" in line:
                if is_fatal_failure:
                    nuke_account_immediately(email)
                elif has_critical_error and not is_already_owned:
                    r.set(f"status:{email}", "❌ 任务异常结束 (超时/失败)", ex=3600)
                    r.set(f"result:{email}", "fail", ex=3600)
                else:
                    pending_game = r.get(f"pending_game:{email}")
                    if pending_game:
                        report_success(email, pending_game)
                    if is_already_owned:
                        r.set(f"status:{email}", "🎉 任务完成 (游戏已在库中)", ex=3600)
                        r.set(f"result:{email}", "success_owned", ex=3600) 
                    else:
                        r.set(f"status:{email}", "🎉 成功领取新游戏！", ex=3600)
                        r.set(f"result:{email}", "success_new", ex=3600)

        process.wait()
        
        # 正常结束，执行常规瘦身
        clean_user_profile(email)

        if mode == 'verify':
            if is_login_success and not is_fatal_failure and not has_critical_error:
                r.set(f"result:{email}", "success", ex=3600)
                r.set(f"status:{email}", "✅ 验证通过", ex=3600)
            else:
                if not r.get(f"result:{email}"):
                    r.set(f"result:{email}", "fail", ex=3600)
                    if not r.get(f"status:{email}"):
                        r.set(f"status:{email}", "❌ 验证失败", ex=3600)

    except Exception as e:
        print(f"Error: {e}")
        r.set(f"status:{email}", "❌ 系统错误", ex=3600)
        r.set(f"result:{email}", "fail", ex=3600)

def main_loop():
    while True:
        task = r.blpop("task_queue", timeout=10)
        if task:
            _, data_json = task
            try: run_task(json.loads(data_json))
            except: pass
        time.sleep(0.1)

if __name__ == "__main__":
    main_loop()