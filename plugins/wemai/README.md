# WeMai 微信网关插件

将微信消息转发到 MaiBot，并将 MaiBot 的回复发送到微信。

> 基于 [Angela459/WeMai](https://github.com/Angela459/WeMai) 改造，适配 MaiBot 1.0+ plugin-sdk 2.7.0。

## 依赖

- `wxauto` - 微信自动化库
- 微信桌面版（推荐 3.9.11.17 32位）
- Windows 系统

## 安装

1. 将 `plugins/wemai/` 目录放入 MaiBot 的 `plugins/` 目录下

2. 安装依赖：

   ```bash
   pip install wxauto
   ```

3. 修改 `config.toml` 配置需要监听的微信群/联系人

4. 启动 MaiBot，插件会自动加载

## 配置

编辑 `plugins/wemai/config.toml`：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `target_chats` | `list[string]` | `[]` | 要监听的聊天对象，为空则根据 `listen_all_if_empty` 决定 |
| `listen_all_if_empty` | `bool` | `false` | 未指定目标聊天时是否监听所有聊天 |
| `excluded_chats` | `list[string]` | 文件传输助手等 | 排除的聊天对象 |
| `image_auto_download` | `bool` | `true` | 是否自动下载聊天中的图片 |

## 使用

1. 确保微信桌面版已登录并保持在前台
2. 确保要监听的聊天窗口在微信中已打开
3. 插件启动后会自动监听配置的聊天对象
4. 微信中收到的消息会被转发到 MaiBot 处理
5. MaiBot 的回复会自动发送到对应的微信聊天

## 测试

1. 在微信中向监听的聊天对象发送消息
2. 观察 MaiBot 日志，确认消息已被接收
3. MaiBot 的回复应能正确发送到微信
