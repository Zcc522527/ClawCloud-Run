# ClawCloud 自动登录

自动登录 ClawCloud 平台并保持会话活跃。

## 功能特点

- ✅ 自动 GitHub OAuth 登录
- ✅ 支持设备验证等待（60秒）
- ✅ 支持二次验证（2FA）等待（60秒）
- ✅ 自动更新 GitHub Session Cookie
- ✅ Telegram 实时通知
- ✅ 失败时自动截图

## 快速开始

### 1. Fork 仓库

点击右上角 **Fork** 按钮

### 2. 配置 Secrets

进入 **Settings** → **Secrets and variables** → **Actions**，添加：

**必需：**
- `GH_USERNAME`: GitHub 用户名
- `GH_PASSWORD`: GitHub 密码

**推荐：**
- `TG_BOT_TOKEN`: Telegram Bot Token
- `TG_CHAT_ID`: Telegram Chat ID
- `REPO_TOKEN`: GitHub Personal Access Token (自动更新 Cookie)

### 3. 启用 Actions

进入 **Actions** → 点击 **I understand my workflows**

### 4. 手动测试

选择 **Auto Login** workflow → **Run workflow**

## 详细文档

查看 [配置指南](docs/CONFIG.md)

## License

MIT
