# 网络设备配置自动备份

这是一套用于 Linux 的 Python 备份脚本，支持通过 SSH 或 Telnet 自动备份华为、思科、飞塔防火墙、华三、信锐交换机、思科 AC、华为 AC 和 SonicWall 防火墙配置。脚本会按区域保存备份文件，文件名包含设备类型、设备角色和 IP。`backup.py` 负责设备备份，`run-backup*.sh` 负责本地清理和远端同步。

## 项目结构

```text
net-config-backup/
  backup.py                 # 主备份脚本
  devices.yaml.example      # 设备清单示例，复制为 devices.yaml 后使用
  requirements.txt          # Python 依赖
  run-backup.sh             # cron 调用入口
  run-backup-synology.sh    # 备份后同步到群晖 NAS 的独立入口
  run-backup-onedrive-client.sh # 备份后使用 abraunegg/onedrive 同步的独立入口
  account-configs/          # 各厂商备份账号配置模板
```

## 1. 安装部署

建议部署到 `/opt/net-config-backup`：

```bash
sudo mkdir -p /opt/net-config-backup
sudo cp -r net-config-backup/* /opt/net-config-backup/
cd /opt/net-config-backup

apt install python3.10-venv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp devices.yaml.example devices.yaml
chmod 600 devices.yaml
```

## 2. 配置设备清单

编辑设备清单：

```bash
vi /opt/net-config-backup/devices.yaml
```

设备通过区域分块管理，同一区域只需要写一次区域码。一台设备写一段配置，结构直观，后续排查也方便。

```yaml
devices:
  office-a:
    - device_type: cisco
      role: switch
      host: 172.16.72.3
      transport: ssh
      username: backup
      password_env: CISCO_BACKUP_PASSWORD
      port: 22
      file_extension: cfg

    - device_type: cisco
      role: switch
      host: 172.16.72.4
      transport: telnet
      username: backup
      password_env: CISCO_BACKUP_PASSWORD
      file_extension: cfg
```

脚本也兼容旧写法：每台设备自己写 `region` 字段。
如果不写 `role`，默认使用 `device`。
如果不写 `transport`，默认使用 `ssh`。

建议把密码放到环境变量中，不要直接写进 `devices.yaml`。

临时测试时可以这样设置：

```bash
export HUAWEI_BACKUP_PASSWORD='your-password'
export CISCO_BACKUP_PASSWORD='your-password'
export CISCO_ENABLE_PASSWORD='your-enable-password'
export FORTIGATE_BACKUP_PASSWORD='your-password'
export H3C_BACKUP_PASSWORD='your-password'
export CISCO_AC_BACKUP_PASSWORD='your-password'
export HUAWEI_AC_BACKUP_PASSWORD='your-password'
export SUNDRAY_BACKUP_PASSWORD='your-password'
export SONICWALL_BACKUP_PASSWORD='your-password'
```

如果要通过 cron 定时运行，建议创建 `/opt/net-config-backup/backup.env`：

```bash
HUAWEI_BACKUP_PASSWORD='your-password'
CISCO_BACKUP_PASSWORD='your-password'
CISCO_ENABLE_PASSWORD='your-enable-password'
FORTIGATE_BACKUP_PASSWORD='your-password'
H3C_BACKUP_PASSWORD='your-password'
CISCO_AC_BACKUP_PASSWORD='your-password'
HUAWEI_AC_BACKUP_PASSWORD='your-password'
SUNDRAY_BACKUP_PASSWORD='your-password'
SONICWALL_BACKUP_PASSWORD='your-password'

# 可选：使用 rclone 时启用 OneDrive 同步和远端清理。
# 如果改用 abraunegg/onedrive，可以留空。
# ONEDRIVE_REMOTE='onedrive:network-config-backups'
ONEDRIVE_REMOTE=''

# 可选：本地和 rclone 远端都按这个天数清理。
RETENTION_DAYS='180'
```

保护密码文件权限：

```bash
sudo chmod 600 /opt/net-config-backup/backup.env
```

## 3. 测试运行

先做一次 dry run，只检查配置文件和设备命令，不会真正登录设备：

```bash
cd /opt/net-config-backup
. .venv/bin/activate
python backup.py --dry-run
```

执行真实备份：

```bash
cd /opt/net-config-backup
set -a
. ./backup.env
set +a
. .venv/bin/activate
python backup.py
```

备份文件保存位置：

```text
/opt/net-config-backup/backups/<区域>/
```

文件名包含备份时间、设备类型、设备角色和 IP，例如：

```text
2026-07-09_000001_cisco_switch_172.16.72.3.cfg
```

日志文件保存位置：

```text
/opt/net-config-backup/logs/
```

## 4. SSH 和 Telnet 混用

脚本默认使用 Netmiko，既可以走 SSH，也可以走 Telnet。区别主要在 `devices.yaml` 里的 `device_type`。

SSH 示例：

```yaml
devices:
  office-a:
    - device_type: cisco
      role: switch
      host: 172.16.72.3
      transport: ssh
      username: backup
      password_env: CISCO_BACKUP_PASSWORD
      port: 22
```

Telnet 示例：

```yaml
devices:
  office-a:
    - device_type: cisco
      role: switch
      host: 172.16.72.3
      transport: telnet
      username: backup
      password_env: CISCO_BACKUP_PASSWORD
      port: 23
```

常用类型：

```text
华为: huawei
思科: cisco
飞塔: fortinet
华三: h3c
思科 AC: cisco_ac
华为 AC: huawei_ac
信锐交换机: sundray
SonicWall 防火墙: sonicwall
```

连接方式由 `transport` 控制：

```text
SSH: ssh
Telnet: telnet
```

如果 `transport: telnet`，脚本会默认使用 `23` 端口；否则默认使用 `22` 端口。你也可以显式写 `port` 覆盖。

脚本内部会自动把这些易读类型转换成 Netmiko 类型，例如 `cisco + ssh` 会转换为 `cisco_ios`，`cisco + telnet` 会转换为 `cisco_ios_telnet`，`h3c + ssh` 会转换为 `hp_comware`。

信锐交换机和 SonicWall 防火墙默认使用 Netmiko 的 `generic` 驱动，适合先跑通 CLI 备份。如果某个型号提示符或分页行为特殊，可以单台设备覆盖 `backup_command`、`pre_backup_commands` 或 `expect_string`。

信锐交换机默认使用 `command_method: timing` 读取输出，不依赖提示符匹配，更适合 `Switch#` 这类通用驱动不稳定的设备。可以按需增加读取时间：

```yaml
devices:
  sundray-zone:
    - device_type: sundray
      role: switch
      host: 172.16.155.2
      transport: telnet
      username: backup
      password_env: SUNDRAY_BACKUP_PASSWORD
      command_method: timing
      read_timeout: 300
      last_read: 8
      username_pattern: "(?:\\(none\\)\\s*)?(?:login|Login|Username|username):"
      password_pattern: "(?:Password|password):"
      expect_string: "Switch#"
      file_extension: cfg
```

`command_method` 支持：

```text
pattern: 默认方式，等待设备提示符返回
timing:  按时间读取输出，不强依赖提示符
```

SonicWall 会在备份前默认执行 `no cli pager session`，关闭当前 SSH 会话分页。关闭分页后建议使用默认的 `pattern` 模式，不需要再配置 `command_method: timing`、`more_pattern` 或 `more_response`：

```yaml
devices:
  B-Factory:
    - device_type: sonicwall
      role: firewall
      host: 100.100.128.1
      transport: ssh
      username: admin
      password_env: SONICWALL_BACKUP_PASSWORD
      pre_backup_commands:
        - no cli pager session
      file_extension: txt
```

SonicWall 内置默认读取超时是 `300` 秒。如果某台 SonicWall 配置特别大，关闭分页后仍然读取超时，可以只给这台设备单独增加更大的 `read_timeout`。

如果备份文件里出现类似下面内容，说明命令被发到了登录提示符，还没有真正登录成功：

```text
(none) login: list running_config
```

信锐 Telnet 登录提示可能是 `(none) login:`，脚本已为 `sundray + telnet` 默认适配。必要时可以在单台设备里覆盖：

```yaml
username_pattern: "(?:\\(none\\)\\s*)?(?:login|Login|Username|username):"
password_pattern: "(?:Password|password):"
expect_string: "Switch#"
```

注意：Telnet 是明文协议，账号密码和配置内容都可能被抓包看到。建议只在内网管理网段使用，并优先逐步切换到 SSH。

## 5. 每周六 00:00 自动运行

给运行脚本增加执行权限：

```bash
sudo chmod +x /opt/net-config-backup/run-backup.sh
```

编辑 root 的 crontab：

```bash
sudo crontab -e
```

加入以下任务：

```cron
0 0 * * 6 /opt/net-config-backup/run-backup.sh
```

这个时间表示每周六 00:00，也就是周六刚开始的时候运行。

## 6. 默认备份命令

```text
华为: display current-configuration
思科: show running-config
飞塔: show full-configuration
华三: display current-configuration
思科 AC: show run-config
华为 AC: display current-configuration
信锐交换机: list running_config
SonicWall 防火墙: show current-config
```

如果某台设备需要特殊命令，可以在 `devices.yaml` 里用 `backup_command` 或 `backup_commands` 单独覆盖。

例如 Cisco Catalyst 9800 AC 更接近 IOS-XE，如果 `cisco_ac` 不适用，可以把设备写成 `device_type: cisco`，或单独设置：

```yaml
backup_command: show running-config
```

Cisco 3500 Series Wireless Controller 属于 AireOS WLC。脚本会先执行 `config paging disable` 关闭当前会话分页；`show run-config` 第一次出现 `Press Enter to continue` 时，脚本会自动发送一次回车。推荐配置：

```yaml
devices:
  wireless-zone:
    - device_type: cisco_ac
      role: ac
      host: 172.16.72.9
      transport: ssh
      username: cisco
      password_env: CISCO_AC_BACKUP_PASSWORD
      pre_backup_commands:
        - config paging disable
      file_extension: cfg
```

默认情况下不需要给 `cisco_ac` 配置 `command_method: timing`。Cisco AC 内置默认读取超时是 `600` 秒，如果某台 WLC 关闭分页后仍然输出很慢，可以只给这台设备单独增加更大的 `read_timeout`。

## 7. 本地和 OneDrive 清理

清理逻辑已经放到 `run-backup.sh` 中，不再由 `backup.py` 执行。

默认保留 `180` 天：

```bash
RETENTION_DAYS=180
```

每次 `run-backup.sh` 运行时会执行：

```text
1. 清理本地 backups/ 下 180 天以前的配置备份
2. 清理本地 logs/ 下 180 天以前的日志
3. 如果配置了 ONEDRIVE_REMOTE，通过 rclone 同步 backups/ 到 OneDrive
4. 如果使用 rclone，清理 OneDrive 目标目录中 180 天以前的远端文件
5. 如果使用 rclone，清理 OneDrive 目标目录中的空目录
```

操作日志会写到：

```text
/opt/net-config-backup/logs/run-backup-YYYY-MM-DD.log
/opt/net-config-backup/logs/onedrive-sync-YYYY-MM-DD.log
/opt/net-config-backup/logs/onedrive-cleanup-YYYY-MM-DD.log
```

如果使用 rclone，并且要先测试 OneDrive 远端清理会删除哪些文件，可以手动执行：

```bash
rclone delete "$ONEDRIVE_REMOTE" --min-age 180d --dry-run --log-level INFO
```

## 8. 同步到 OneDrive

Python 脚本只负责备份设备配置，OneDrive 上传由独立同步工具处理。可以二选一：

```text
方案 A: rclone
适合管理员允许 rclone 应用授权的环境。

方案 B: abraunegg/onedrive
适合 rclone 被管理员限制，但允许 OneDrive Client for Linux 授权的环境。
```

### 8.1 方案 A：使用 rclone

安装 rclone：

```bash
sudo apt update
sudo apt install -y rclone
```

配置 OneDrive：

```bash
rclone config
```

创建 remote 时可以命名为 `onedrive`。配置完成后测试：

```bash
rclone lsd onedrive:
```

手动测试上传：

```bash
rclone copy /opt/net-config-backup/backups onedrive:network-config-backups \
  --create-empty-src-dirs \
  --log-file /opt/net-config-backup/logs/onedrive-sync.log \
  --log-level INFO
```

如果希望每次定时备份后自动上传，只需要在 `/opt/net-config-backup/backup.env` 里设置：

```bash
ONEDRIVE_REMOTE='onedrive:network-config-backups'
```

`run-backup.sh` 会自动执行 `rclone copy` 和远端 180 天清理。同步使用 `copy`，不会因为本地缺少某个文件就删除 OneDrive 端文件；远端删除只由 `rclone delete --min-age 180d` 控制。

### 8.2 方案 B：使用 abraunegg/onedrive

如果公司管理员限制了 rclone，可以改用 `abraunegg/onedrive`。这个方案建议使用一次性同步，不建议长期 `--monitor` 常驻运行：

```text
备份完成 -> 本地清理 180 天以前文件 -> onedrive 单向上传同步
```

官方文档：

- 安装文档：<https://github.com/abraunegg/onedrive/blob/master/docs/ubuntu-package-install.md>
- 使用文档：<https://github.com/abraunegg/onedrive/blob/master/docs/usage.md>

#### 8.2.1 安装 onedrive

先确认系统版本：

```bash
lsb_release -a
```

Ubuntu 22.04：

```bash
wget -qO - https://download.opensuse.org/repositories/home:/npreining:/debian-ubuntu-onedrive/xUbuntu_22.04/Release.key | gpg --dearmor | sudo tee /usr/share/keyrings/obs-onedrive.gpg > /dev/null

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/obs-onedrive.gpg] https://download.opensuse.org/repositories/home:/npreining:/debian-ubuntu-onedrive/xUbuntu_22.04/ ./" | sudo tee /etc/apt/sources.list.d/onedrive.list

sudo apt-get update
sudo apt install --no-install-recommends --no-install-suggests onedrive
```

Ubuntu 24.04 把命令里的 `xUbuntu_22.04` 改成 `xUbuntu_24.04`。Debian 12 改成 `Debian_12`。

#### 8.2.2 创建专用配置目录

建议给本项目单独创建配置目录，不使用默认 `~/.config/onedrive`：

```bash
sudo mkdir -p /opt/net-config-backup/onedrive-conf
sudo chmod 700 /opt/net-config-backup/onedrive-conf
```

创建配置文件：

```bash
sudo nano /opt/net-config-backup/onedrive-conf/config
```

写入：

```ini
sync_dir = "/opt/net-config-backup"
skip_file = "~*|.~*|*.tmp"
skip_dir = ".venv|__pycache__|logs"
enable_logging = "true"
log_dir = "/opt/net-config-backup/logs"
```

只同步 `backups/` 目录：

```bash
sudo tee /opt/net-config-backup/onedrive-conf/sync_list > /dev/null <<'EOF'
/backups/
/backups/*
EOF
```

这样 OneDrive 里会出现 `backups` 文件夹，目录结构仍然保持为：

```text
backups/<区域>/YYYY-MM-DD_HHMMSS_<设备类型>_<设备角色>_<IP>.cfg
```

#### 8.2.3 登录授权

执行：

```bash
onedrive --confdir="/opt/net-config-backup/onedrive-conf"
```

终端会给出一个授权 URL。复制到浏览器打开，登录要使用的 Microsoft 账号。授权完成后，把浏览器最终跳转后的完整 URL 粘回终端。

如果仍然出现“需要管理员批准”，说明租户限制了第三方应用授权，需要管理员批准 `OneDrive Client for Linux` 这个应用。

#### 8.2.4 同步到他人共享给你的 OneDrive 文件夹

如果使用 `hp.tan@qq.com` 登录，但要写入 `meeting@qq.com` 创建并共享给你的目录，建议先在 OneDrive 网页端操作：

```text
1. 登录 hp.tan@qq.com
2. 打开“共享”
3. 找到 meeting@qq.com 共享给你的文件夹
4. 选择“添加快捷方式到我的文件”
```

添加后，这个共享文件夹会更容易被 Linux onedrive 客户端当作你 OneDrive 里的路径同步。

#### 8.2.5 测试配置

查看配置：

```bash
onedrive --confdir="/opt/net-config-backup/onedrive-conf" --display-config
```

先 dry-run：

```bash
onedrive --confdir="/opt/net-config-backup/onedrive-conf" --sync --upload-only --dry-run --verbose
```

确认没问题后正式同步：

```bash
onedrive --confdir="/opt/net-config-backup/onedrive-conf" --sync --upload-only --verbose
```

`--upload-only` 表示以本地为准进行单向上传。由于 `run-backup.sh` 会先清理本地 180 天以前的备份文件，后续 `--upload-only` 会把这些删除同步到 OneDrive，因此远端也会保持同样的保留周期。

如果希望 OneDrive 只增加文件、不删除远端旧文件，可以加 `--no-remote-delete`：

```bash
onedrive --confdir="/opt/net-config-backup/onedrive-conf" --sync --upload-only --no-remote-delete --verbose
```

但这样远端就不会自动遵守 180 天保留策略。

#### 8.2.6 使用独立 onedrive 客户端脚本

项目已提供独立脚本：

```text
/opt/net-config-backup/run-backup-onedrive-client.sh
```

如果改用 `abraunegg/onedrive`，建议在 `/opt/net-config-backup/backup.env` 中保持 `ONEDRIVE_REMOTE` 为空，避免原始 `run-backup.sh` 继续触发 rclone：

```bash
ONEDRIVE_REMOTE=''
ONEDRIVE_CONFDIR='/opt/net-config-backup/onedrive-conf'

# 可选：true 表示远端旧文件不跟随本地删除。
ONEDRIVE_NO_REMOTE_DELETE='false'
```

给脚本增加执行权限：

```bash
sudo chmod +x /opt/net-config-backup/run-backup-onedrive-client.sh
```

手动测试：

```bash
/opt/net-config-backup/run-backup-onedrive-client.sh
```

定时任务可以改成：

```cron
0 0 * * 6 /opt/net-config-backup/run-backup-onedrive-client.sh
```

脚本会先运行 `backup.py`，再清理本地 180 天以前的 `backups/` 和 `logs/`，最后执行 `onedrive --sync --upload-only`。

### 8.3 同步到群晖 NAS

项目已提供独立脚本：

```text
/opt/net-config-backup/run-backup-synology.sh
```

推荐使用 rclone 的 `sftp` remote 连接群晖。先在群晖 DSM 中启用 SFTP，并创建专用账号和共享文件夹。假设 rclone remote 名称为 `synology`，群晖共享目录为 `NetConfigBackup`，在 `/opt/net-config-backup/backup.env` 中加入：

```bash
NAS_REMOTE='synology:NetConfigBackup/backups'
RETENTION_DAYS='180'
```

手动测试 rclone：

```bash
rclone lsd synology:
rclone copy /opt/net-config-backup/backups synology:NetConfigBackup/backups --create-empty-src-dirs --dry-run
```

确认无误后给脚本增加执行权限：

```bash
sudo chmod +x /opt/net-config-backup/run-backup-synology.sh
```

手动测试：

```bash
/opt/net-config-backup/run-backup-synology.sh
```

定时任务可以改成：

```cron
0 0 * * 6 /opt/net-config-backup/run-backup-synology.sh
```

脚本会先运行 `backup.py`，再清理本地 180 天以前的 `backups/` 和 `logs/`，然后执行：

```text
1. rclone copy backups/ 到 NAS_REMOTE
2. rclone delete 删除 NAS_REMOTE 中 180 天以前的远端文件
3. rclone rmdirs 清理 NAS_REMOTE 中的空目录
```

## 9. 安全建议

网络设备配置可能包含敏感信息。建议至少做到：

- 备份账号使用只读权限。
- `devices.yaml` 和 `backup.env` 权限设置为 `600`。
- OneDrive 如果存放生产设备配置，建议确认公司合规要求；rclone 可以使用 `crypt` remote，加密后再上传。
- 不要把备份目录放在公开 Web 目录下。
