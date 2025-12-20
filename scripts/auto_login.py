#!/usr/bin/env python3
"""
ClawCloud 自动登录脚本
- 支持设备验证等待
- 支持二次验证（2FA）等待
- 自动更新 GitHub Secret
- Telegram 通知
"""

import os
import sys
import time
import base64
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== 配置 ====================
CLAW_CLOUD_URL = "https://eu-central-1.run.claw.cloud"
SIGNIN_URL = f"{CLAW_CLOUD_URL}/signin"

# 等待时间配置（支持环境变量）
DEVICE_VERIFY_WAIT = int(os.environ.get('DEVICE_VERIFY_WAIT', '60'))
TWO_FACTOR_WAIT = int(os.environ.get('TWO_FACTOR_WAIT', '60'))
REDIRECT_WAIT = int(os.environ.get('REDIRECT_WAIT', '90'))

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 5


class Telegram:
    """Telegram 通知"""
    
    def __init__(self):
        self.token = os.environ.get('TG_BOT_TOKEN')
        self.chat_id = os.environ.get('TG_CHAT_ID')
        self.ok = bool(self.token and self.chat_id)
        if self.ok:
            print("✅ Telegram 通知已启用")
    
    def send(self, msg):
        if not self.ok:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=30
            )
        except Exception as e:
            print(f"⚠️ Telegram 发送失败: {e}")
    
    def photo(self, path, caption=""):
        if not self.ok or not os.path.exists(path):
            return
        try:
            with open(path, 'rb') as f:
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption[:1024]},
                    files={"photo": f},
                    timeout=60
                )
        except Exception as e:
            print(f"⚠️ Telegram 图片发送失败: {e}")


class SecretUpdater:
    """GitHub Secret 更新器"""
    
    def __init__(self):
        self.token = os.environ.get('REPO_TOKEN')
        self.repo = os.environ.get('GITHUB_REPOSITORY')
        self.ok = bool(self.token and self.repo)
        if self.ok:
            print(f"✅ Secret 自动更新已启用: {self.repo}")
        else:
            print("⚠️ Secret 自动更新未启用（需要 REPO_TOKEN）")
    
    def update(self, name, value):
        if not self.ok:
            return False
        try:
            from nacl import encoding, public
            
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # 获取公钥
            r = requests.get(
                f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key",
                headers=headers, timeout=30
            )
            if r.status_code != 200:
                print(f"❌ 获取公钥失败: {r.status_code}")
                return False
            
            key_data = r.json()
            pk = public.PublicKey(key_data['key'].encode(), encoding.Base64Encoder())
            encrypted = public.SealedBox(pk).encrypt(value.encode())
            
            # 更新 Secret
            r = requests.put(
                f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}",
                headers=headers,
                json={
                    "encrypted_value": base64.b64encode(encrypted).decode(),
                    "key_id": key_data['key_id']
                },
                timeout=30
            )
            
            if r.status_code in [201, 204]:
                print(f"✅ Secret {name} 更新成功")
                return True
            else:
                print(f"❌ Secret 更新失败: {r.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 更新 Secret 异常: {e}")
            return False


class AutoLogin:
    """自动登录"""
    
    def __init__(self):
        self.username = os.environ.get('GH_USERNAME')
        self.password = os.environ.get('GH_PASSWORD')
        self.gh_session = os.environ.get('GH_SESSION', '').strip()
        self.tg = Telegram()
        self.secret = SecretUpdater()
        self.shots = []
        self.logs = []
        self.step_count = 0
        self.shot_count = 0
        
    def log(self, msg, level="INFO"):
        """统一日志输出"""
        icons = {
            "INFO": "ℹ️",
            "SUCCESS": "✅",
            "ERROR": "❌",
            "WARN": "⚠️",
            "STEP": "🔹",
            "WAIT": "⏳"
        }
        line = f"{icons.get(level, '•')} {msg}"
        print(line)
        self.logs.append(line)
    
    def shot(self, page, name):
        """截图"""
        self.shot_count += 1
        filename = f"{self.shot_count:02d}_{name}.png"
        try:
            page.screenshot(path=filename, full_page=True)
            self.shots.append(filename)
            self.log(f"截图: {filename}")
            return filename
        except Exception as e:
            self.log(f"截图失败: {e}", "WARN")
            return None
    
    def safe_click(self, page, selectors, desc="按钮"):
        """安全点击"""
        for selector in selectors:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=3000):
                    element.click()
                    self.log(f"已点击: {desc}", "SUCCESS")
                    return True
            except:
                continue
        self.log(f"未找到: {desc}", "WARN")
        return False
    
    def get_session_cookie(self, context):
        """提取 GitHub Session Cookie"""
        try:
            for cookie in context.cookies():
                if cookie['name'] == 'user_session' and 'github' in cookie.get('domain', ''):
                    return cookie['value']
        except Exception as e:
            self.log(f"获取 Cookie 失败: {e}", "WARN")
        return None
    
    def save_new_cookie(self, value):
        """保存新 Cookie"""
        if not value or value == self.gh_session:
            return
        
        self.log(f"新 Cookie: {value[:12]}...{value[-8:]}", "SUCCESS")
        
        # 尝试自动更新 Secret
        if self.secret.update('GH_SESSION', value):
            self.tg.send(f"""🔑 <b>Cookie 已自动更新</b>

仓库: <code>{self.secret.repo}</code>
时间: {time.strftime('%Y-%m-%d %H:%M:%S')}

新值: <code>{value[:20]}...{value[-10:]}</code>""")
        else:
            # 通过 Telegram 发送完整 Cookie
            self.tg.send(f"""🔑 <b>新的 GitHub Session Cookie</b>

请手动更新 Secret <b>GH_SESSION</b>:

<code>{value}</code>

⚠️ 此消息包含敏感信息，请在更新后删除""")
    
    def wait_for_verification(self, page, verify_type="device"):
        """统一的验证等待逻辑"""
        wait_time = DEVICE_VERIFY_WAIT if verify_type == "device" else TWO_FACTOR_WAIT
        
        if verify_type == "device":
            title = "设备验证"
            check_patterns = ['verified-device', 'device-verification']
            msg = f"""⚠️ <b>需要设备验证</b>

请在 {wait_time} 秒内批准：
1️⃣ 检查邮箱并点击验证链接
2️⃣ 或在 GitHub App 中批准设备
3️⃣ 或访问 https://github.com/settings/security"""
        else:
            title = "二次验证 (2FA)"
            check_patterns = ['two-factor', 'sessions/two-factor']
            msg = f"""⚠️ <b>需要二次验证 (2FA)</b>

请在 {wait_time} 秒内完成：
1️⃣ 打开验证器 App（如 Google Authenticator）
2️⃣ 获取 6 位验证码
3️⃣ 在页面中输入并提交"""
        
        self.log(f"需要{title}，等待 {wait_time} 秒...", "WAIT")
        shot_file = self.shot(page, title)
        
        # 发送通知
        self.tg.send(msg)
        if shot_file:
            self.tg.photo(shot_file, f"{title}页面")
        
        # 等待循环
        for i in range(wait_time):
            time.sleep(1)
            
            # 每 5 秒检查一次
            if i % 5 == 0:
                progress = f"{i}/{wait_time}秒"
                self.log(f"  等待{title}... ({progress})", "WAIT")
                
                try:
                    current_url = page.url
                    
                    # 检查是否已离开验证页面
                    if not any(pattern in current_url for pattern in check_patterns):
                        self.log(f"{title}通过！", "SUCCESS")
                        self.tg.send(f"✅ <b>{title}通过</b>")
                        time.sleep(2)
                        page.wait_for_load_state('networkidle', timeout=15000)
                        self.shot(page, f"{title}_通过")
                        return True
                    
                    # 尝试刷新页面状态
                    if i % 10 == 0 and i > 0:
                        page.evaluate("() => { }")  # 触发页面检查
                        
                except Exception as e:
                    self.log(f"检查异常: {e}", "WARN")
        
        # 超时后最终检查
        try:
            final_url = page.url
            if not any(pattern in final_url for pattern in check_patterns):
                self.log(f"{title}完成！", "SUCCESS")
                return True
        except:
            pass
        
        self.log(f"{title}超时", "ERROR")
        self.tg.send(f"❌ <b>{title}超时</b>\n\n请检查是否已完成验证")
        return False
    
    def login_github(self, page, context):
        """登录 GitHub"""
        self.log("=== 开始 GitHub 登录 ===", "STEP")
        self.shot(page, "GitHub登录页")
        
        # 检查是否已经登录
        url = page.url
        if 'login' not in url and 'session' not in url:
            self.log("已通过 Cookie 登录", "SUCCESS")
            return True
        
        # 输入凭据
        try:
            self.log("输入用户名和密码...")
            page.locator('input[name="login"]').fill(self.username)
            page.locator('input[name="password"]').fill(self.password)
            self.shot(page, "GitHub已填写")
            
            # 提交表单
            page.locator('input[type="submit"], button[type="submit"]').first.click()
            self.log("已提交登录表单")
            
        except Exception as e:
            self.log(f"输入凭据失败: {e}", "ERROR")
            return False
        
        # 等待页面加载
        time.sleep(3)
        try:
            page.wait_for_load_state('networkidle', timeout=30000)
        except PlaywrightTimeout:
            self.log("页面加载超时，继续...", "WARN")
        
        self.shot(page, "GitHub登录后")
        url = page.url
        self.log(f"当前 URL: {url}")
        
        # 检查设备验证
        if 'verified-device' in url or 'device-verification' in url:
            if not self.wait_for_verification(page, "device"):
                return False
            url = page.url
        
        # 检查二次验证
        if 'two-factor' in url or 'sessions/two-factor' in url:
            if not self.wait_for_verification(page, "2fa"):
                return False
            url = page.url
        
        # 检查错误消息
        try:
            error_elem = page.locator('.flash-error, .js-flash-alert').first
            if error_elem.is_visible(timeout=2000):
                error_text = error_elem.inner_text()
                self.log(f"登录错误: {error_text}", "ERROR")
                return False
        except:
            pass
        
        # 检查是否需要额外操作
        if 'login' in url or 'session' in url:
            self.log("登录可能未完成", "WARN")
            self.shot(page, "GitHub登录状态")
        
        self.log("GitHub 登录流程完成", "SUCCESS")
        return True
    
    def handle_oauth(self, page):
        """处理 OAuth 授权"""
        if 'github.com/login/oauth/authorize' not in page.url:
            return
        
        self.log("=== 处理 OAuth 授权 ===", "STEP")
        self.shot(page, "OAuth授权")
        
        # 点击授权按钮
        if self.safe_click(page, [
            'button[name="authorize"]',
            'button:has-text("Authorize")',
            'input[name="authorize"]'
        ], "授权"):
            time.sleep(3)
            try:
                page.wait_for_load_state('networkidle', timeout=30000)
            except PlaywrightTimeout:
                self.log("OAuth 跳转超时，继续...", "WARN")
    
    def wait_for_redirect(self, page, target='claw.cloud', max_wait=None):
        """等待重定向到目标页面"""
        max_wait = max_wait or REDIRECT_WAIT
        self.log(f"=== 等待重定向到 {target} ===", "STEP")
        
        for i in range(max_wait):
            url = page.url
            
            # 成功重定向
            if target in url and 'signin' not in url.lower():
                self.log(f"重定向成功！({i}秒)", "SUCCESS")
                return True
            
            # 处理中间的 OAuth
            if 'github.com/login/oauth/authorize' in url:
                self.handle_oauth(page)
            
            # 每 10 秒输出日志
            if i % 10 == 0:
                self.log(f"  等待重定向... ({i}/{max_wait}秒)")
                self.log(f"  当前: {url[:80]}...")
            
            time.sleep(1)
        
        self.log("重定向超时", "ERROR")
        self.shot(page, "重定向超时")
        return False
    
    def perform_keepalive(self, page):
        """保活操作"""
        self.log("=== 执行保活操作 ===", "STEP")
        
        keepalive_urls = [
            (f"{CLAW_CLOUD_URL}/", "控制台首页"),
            (f"{CLAW_CLOUD_URL}/apps", "应用列表"),
        ]
        
        for url, name in keepalive_urls:
            try:
                self.log(f"访问: {name}...")
                page.goto(url, timeout=30000)
                page.wait_for_load_state('networkidle', timeout=15000)
                self.log(f"✓ {name}", "SUCCESS")
                time.sleep(2)
            except Exception as e:
                self.log(f"访问 {name} 失败: {e}", "WARN")
        
        self.shot(page, "保活完成")
    
    def send_final_notification(self, success, error_msg=""):
        """发送最终通知"""
        if not self.tg.ok:
            return
        
        status = "✅ 成功" if success else "❌ 失败"
        
        msg = f"""<b>🤖 ClawCloud 自动登录</b>

<b>状态:</b> {status}
<b>用户:</b> <code>{self.username}</code>
<b>时间:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}
<b>截图:</b> {len(self.shots)} 张"""
        
        if error_msg:
            msg += f"\n<b>错误:</b> {error_msg}"
        
        # 添加最近日志
        msg += "\n\n<b>最近日志:</b>"
        for log in self.logs[-8:]:
            msg += f"\n{log}"
        
        self.tg.send(msg)
        
        # 发送关键截图
        if self.shots:
            if success:
                # 成功时发送最后一张
                self.tg.photo(self.shots[-1], "登录成功")
            else:
                # 失败时发送最后 3 张
                for shot in self.shots[-3:]:
                    self.tg.photo(shot, os.path.basename(shot))
    
    def run(self):
        """主流程"""
        print("\n" + "="*60)
        print("🚀 ClawCloud 自动登录脚本")
        print("="*60)
        print(f"版本: 2.0")
        print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60 + "\n")
        
        # 配置检查
        self.log(f"用户名: {self.username}")
        self.log(f"密码: {'●' * 8 if self.password else '未设置'}")
        self.log(f"Session Cookie: {'已设置' if self.gh_session else '未设置'}")
        self.log(f"设备验证等待: {DEVICE_VERIFY_WAIT}秒")
        self.log(f"2FA 等待: {TWO_FACTOR_WAIT}秒")
        self.log(f"重定向等待: {REDIRECT_WAIT}秒")
        
        if not self.username or not self.password:
            self.log("缺少必要的凭据配置", "ERROR")
            self.send_final_notification(False, "凭据未配置")
            sys.exit(1)
        
        print()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            page = context.new_page()
            
            try:
                # 预加载 Cookie
                if self.gh_session:
                    try:
                        context.add_cookies([
                            {
                                'name': 'user_session',
                                'value': self.gh_session,
                                'domain': '.github.com',
                                'path': '/'
                            },
                            {
                                'name': 'logged_in',
                                'value': 'yes',
                                'domain': '.github.com',
                                'path': '/'
                            }
                        ])
                        self.log("已加载 GitHub Session Cookie", "SUCCESS")
                    except Exception as e:
                        self.log(f"加载 Cookie 失败: {e}", "WARN")
                
                # Step 1: 访问 ClawCloud
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 访问 ClawCloud", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                page.goto(SIGNIN_URL, timeout=60000)
                page.wait_for_load_state('networkidle', timeout=30000)
                time.sleep(2)
                self.shot(page, "ClawCloud首页")
                
                # 检查是否已登录
                if 'signin' not in page.url.lower():
                    self.log("检测到已登录状态！", "SUCCESS")
                    self.perform_keepalive(page)
                    
                    # 更新 Cookie
                    new_cookie = self.get_session_cookie(context)
                    if new_cookie:
                        self.save_new_cookie(new_cookie)
                    
                    self.send_final_notification(True)
                    print("\n" + "="*60)
                    print("✅ 登录成功（使用现有 Session）")
                    print("="*60 + "\n")
                    return
                
                # Step 2: 点击 GitHub 登录
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 点击 GitHub 登录", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                if not self.safe_click(page, [
                    'button:has-text("GitHub")',
                    'a:has-text("GitHub")',
                    '[data-provider="github"]',
                    'button[data-test="github-login"]'
                ], "GitHub 登录按钮"):
                    self.log("找不到 GitHub 登录按钮", "ERROR")
                    self.shot(page, "找不到按钮")
                    self.send_final_notification(False, "找不到 GitHub 登录按钮")
                    sys.exit(1)
                
                time.sleep(3)
                page.wait_for_load_state('networkidle', timeout=30000)
                self.shot(page, "点击GitHub后")
                
                # Step 3: GitHub 认证
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: GitHub 认证", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                url = page.url
                self.log(f"当前 URL: {url}")
                
                if 'github.com/login' in url or 'github.com/session' in url:
                    # 需要登录
                    if not self.login_github(page, context):
                        self.send_final_notification(False, "GitHub 登录失败")
                        sys.exit(1)
                elif 'github.com/login/oauth/authorize' in url:
                    # Cookie 有效，直接授权
                    self.log("Session Cookie 有效", "SUCCESS")
                    self.handle_oauth(page)
                else:
                    self.log(f"未预期的页面: {url}", "WARN")
                
                # Step 4: 等待重定向
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 等待重定向", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                if not self.wait_for_redirect(page):
                    self.send_final_notification(False, "重定向超时")
                    sys.exit(1)
                
                self.shot(page, "重定向成功")
                
                # Step 5: 验证登录状态
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 验证登录状态", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                final_url = page.url
                if 'claw.cloud' not in final_url or 'signin' in final_url.lower():
                    self.log(f"登录验证失败: {final_url}", "ERROR")
                    self.send_final_notification(False, "登录验证失败")
                    sys.exit(1)
                
                self.log("登录状态验证成功", "SUCCESS")
                
                # Step 6: 保活操作
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 保活操作", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                self.perform_keepalive(page)
                
                # Step 7: 更新 Cookie
                self.step_count += 1
                self.log(f"\n{'='*50}", "STEP")
                self.log(f"步骤 {self.step_count}: 更新 Session Cookie", "STEP")
                self.log(f"{'='*50}", "STEP")
                
                new_cookie = self.get_session_cookie(context)
                if new_cookie:
                    self.save_new_cookie(new_cookie)
                else:
                    self.log("未获取到新 Cookie", "WARN")
                
                # 成功通知
                self.send_final_notification(True)
                
                print("\n" + "="*60)
                print("✅ 所有步骤完成！登录成功！")
                print("="*60 + "\n")
                
            except KeyboardInterrupt:
                self.log("用户中断", "WARN")
                self.send_final_notification(False, "用户中断")
                sys.exit(130)
                
            except Exception as e:
                self.log(f"发生异常: {e}", "ERROR")
                self.shot(page, "异常错误")
                
                import traceback
                traceback.print_exc()
                
                self.send_final_notification(False, str(e))
                sys.exit(1)
                
            finally:
                browser.close()
                
                # 清理截图（可选）
                # for shot in self.shots:
                #     try:
                #         os.remove(shot)
                #     except:
                #         pass


if __name__ == "__main__":
    AutoLogin().run()
