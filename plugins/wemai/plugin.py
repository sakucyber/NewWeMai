import asyncio
import base64
import hashlib
import os
import tempfile
import threading
import time
from typing import Any

from wxauto import WeChat
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, MaiBotPlugin, MessageGateway, PluginConfigBase, Field


class WxConfig(PluginConfigBase):
    target_chats: list[str] = Field(default_factory=list, description="要监听的微信聊天对象，为空则根据 listen_all_if_empty 决定")
    listen_all_if_empty: bool = Field(default=False, description="未指定目标聊天时是否监听所有聊天")
    excluded_chats: list[str] = Field(default=["文件传输助手", "微信团队", "微信支付"], description="排除的聊天对象")
    image_auto_download: bool = Field(default=True, description="是否自动下载聊天中的图片")


class WeMaiConfig(PluginConfigBase):
    wx: WxConfig = Field(default_factory=WxConfig, description="微信配置")


class WeMaiPlugin(MaiBotPlugin):
    config_model = WeMaiConfig

    def __init__(self):
        super().__init__()
        self._listener_task: asyncio.Task | None = None
        self._stop_event = threading.Event()
        self._wechat: WeChat | None = None
        self._wechat_lock = threading.Lock()
        self._listener_initialized = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def on_load(self) -> None:
        self.ctx.logger.info("WeMai 微信网关插件加载中...")
        self._loop = asyncio.get_event_loop()
        try:
            await self.ctx.gateway.update_state(
                gateway_name="wemai_gateway",
                ready=True,
                platform="wx",
                account_id="wemai",
                scope="primary",
                metadata={"protocol": "wxauto"},
            )
        except Exception as e:
            self.ctx.logger.warning("上报网关状态失败（不影响运行）: %s", e)
        self._start_listener()
        self.ctx.logger.info("WeMai 微信网关插件加载完成")

    async def on_unload(self) -> None:
        self.ctx.logger.info("WeMai 微信网关插件卸载中...")
        self._stop_event.set()
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._listener_initialized = False
        await self.ctx.gateway.update_state(
            gateway_name="wemai_gateway",
            ready=False,
        )
        self.ctx.logger.info("WeMai 微信网关插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            self.ctx.logger.info("WeMai 配置已更新: version=%s", version)
            self._stop_listener()
            self._start_listener()

    @MessageGateway(
        route_type="duplex",
        name="wemai_gateway",
        platform="wx",
        protocol="wxauto",
        account_id="wemai",
        scope="primary",
    )
    async def send_to_wechat(
        self,
        message: dict[str, Any],
        route: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        msg_info = message.get("message_info", {}) or {}
        msg_seg = message.get("message_segment", {}) or {}

        group_info = msg_info.get("group_info", {}) or {}
        user_info = msg_info.get("user_info", {}) or {}

        receiver = group_info.get("group_name", "") or user_info.get("user_nickname", "")
        content = msg_seg.get("data", "") if msg_seg else ""

        if not receiver or not content:
            return {"success": False, "error": "缺少接收者或消息内容"}

        try:
            await asyncio.get_event_loop().run_in_executor(None, self._send_wx_message, receiver, content)
            return {"success": True, "external_message_id": f"wx-{int(time.time())}"}
        except Exception as e:
            self.ctx.logger.error("发送微信消息失败: %s", e)
            return {"success": False, "error": str(e)}

    def _send_wx_message(self, receiver: str, content: str):
        try:
            with self._wechat_lock:
                if self._wechat is None:
                    self._wechat = WeChat()

            is_base64_image = (
                isinstance(content, str)
                and (
                    content.startswith("data:image/")
                    or (
                        len(content) > 1000
                        and content.replace("+", "").replace("/", "").replace("=", "").isalnum()
                    )
                )
            )

            if is_base64_image:
                try:
                    file_extension = ".png"
                    if content.startswith("data:image/"):
                        header, encoded = content.split(",", 1)
                        if "gif" in header.lower():
                            file_extension = ".gif"
                        elif "jpeg" in header.lower() or "jpg" in header.lower():
                            file_extension = ".jpg"
                        elif "png" in header.lower():
                            file_extension = ".png"
                        image_data = base64.b64decode(encoded)
                    else:
                        image_data = base64.b64decode(content)
                        if image_data.startswith(b"GIF8"):
                            file_extension = ".gif"
                        elif image_data.startswith(b"\xff\xd8\xff"):
                            file_extension = ".jpg"
                        elif image_data.startswith(b"\x89PNG"):
                            file_extension = ".png"

                    with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as f:
                        f.write(image_data)
                        temp_path = f.name

                    self._wechat.SendFiles(temp_path, receiver)
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                    self.ctx.logger.info("已发送图片消息到微信: %s", receiver)
                    return
                except Exception:
                    pass

            if (
                isinstance(content, str)
                and os.path.exists(content)
                and content.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp"))
            ):
                self._wechat.SendFiles(content, receiver)
                self.ctx.logger.info("已发送文件到微信: %s", receiver)
            else:
                self._wechat.SendMsg(content, receiver)
                self.ctx.logger.info("已发送文字消息到微信: %s", receiver)
        except Exception as e:
            self.ctx.logger.error("发送微信消息异常: %s", e)
            raise

    def _start_listener(self):
        self._stop_event.clear()
        self._listener_task = asyncio.create_task(self._run_listener())

    def _stop_listener(self):
        self._stop_event.set()
        if self._listener_task:
            self._listener_task.cancel()

    async def _run_listener(self):
        loop = asyncio.get_event_loop()
        retry_delay = 1
        max_delay = 60
        while not self._stop_event.is_set():
            try:
                await loop.run_in_executor(None, self._listen_cycle)
                retry_delay = 1
            except Exception as e:
                self.ctx.logger.warning("微信监听循环异常: %s，将在 %ds 后重试", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    def _init_listener(self):
        with self._wechat_lock:
            if self._wechat is None:
                self._wechat = WeChat()
            wx = self._wechat

        self.ctx.logger.info("微信登录账号: %s", wx.nickname)
        cfg = self.config
        wx_cfg = cfg.wx

        if wx_cfg.target_chats:
            for chat in wx_cfg.target_chats:
                try:
                    wx.ChatWith(chat)
                    wx.AddListenChat(chat, savepic=wx_cfg.image_auto_download)
                    self.ctx.logger.info("添加监听聊天: %s", chat)
                except Exception as e:
                    self.ctx.logger.warning("添加监听聊天 %s 失败: %s", chat, e)
        elif wx_cfg.listen_all_if_empty:
            sessions = wx.GetSessionList(reset=True)
            for chat in sessions:
                if chat not in wx_cfg.excluded_chats:
                    try:
                        wx.AddListenChat(chat, savepic=wx_cfg.image_auto_download)
                    except Exception:
                        pass
        self._listener_initialized = True

    def _listen_cycle(self):
        if not self._listener_initialized:
            self._init_listener()

        with self._wechat_lock:
            wx = self._wechat

        if wx is None:
            time.sleep(1)
            return

        all_messages = wx.GetListenMessage()
        if not all_messages:
            time.sleep(1)
            return

        for chat_win, messages in all_messages.items():
            chat_name = chat_win.who
            if not messages:
                continue
            for msg in messages:
                self._process_wx_message(chat_name, msg)

        time.sleep(1)

    def _process_wx_message(self, chat_name: str, msg):
        try:
            msg_type = msg.type
            sender = msg.sender
            content = msg.content

            if msg_type == "sys":
                if "以下为新消息" in content or "新消息" in content:
                    return
                if len(content.strip()) <= 10 and ":" in content:
                    return
            if msg_type == "self" or sender == "Self":
                return

            is_group = chat_name != sender
            user_id_hash = hashlib.md5(sender.encode("utf-8")).hexdigest()
            chat_id_hash = hashlib.md5(chat_name.encode("utf-8")).hexdigest()
            raw_content = content

            if os.getenv("IMAGE_RECOGNITION_ENABLED", "true").lower() == "true" and self._is_image_path(content):
                try:
                    if os.path.exists(content):
                        with open(content, "rb") as f:
                            image_data = f.read()
                        raw_content = base64.b64encode(image_data).decode("utf-8")
                except Exception:
                    pass

            message_payload: dict[str, object] = {
                "message_id": hashlib.md5(
                    f"{sender}_{chat_name}_{time.time()}_{content[:20]}".encode("utf-8")
                ).hexdigest(),
                "platform": "wx",
                "message_info": {
                    "user_info": {
                        "user_id": user_id_hash,
                        "user_nickname": sender,
                    },
                    "additional_config": {},
                },
                "raw_message": raw_content,
            }

            if is_group:
                message_payload["message_info"]["group_info"] = {
                    "group_id": chat_id_hash,
                    "group_name": chat_name,
                }
                message_payload["message_info"]["user_info"]["user_cardname"] = sender

            external_id = f"wx-{chat_id_hash[:8]}-{int(time.time() * 1000)}"

            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._inject_message(message_payload, external_id),
                    self._loop,
                )
        except Exception as e:
            self.ctx.logger.error("处理微信消息异常: %s", e)

    def _is_image_path(self, content: str) -> bool:
        if not content:
            return False
        has_sep = "\\" in content or "/" in content
        has_img_ext = any(ext in content.lower() for ext in [".jpg", ".png", ".gif", ".bmp", ".jpeg"])
        has_wx_path = "wxauto文件" in content or "微信图片_" in content
        return (has_sep and has_img_ext) or has_wx_path

    async def _inject_message(self, payload: dict, external_id: str):
        try:
            accepted = await self.ctx.gateway.route_message(
                gateway_name="wemai_gateway",
                message_dict=payload,
                route_metadata={"self_id": "wemai", "connection_id": "primary"},
                external_message_id=external_id,
                dedupe_key=external_id,
            )
            if not accepted:
                self.ctx.logger.warning("Host 未接收入站消息: %s", external_id)
        except Exception as e:
            self.ctx.logger.error("注入消息到 Host 失败: %s", e)


def create_plugin():
    return WeMaiPlugin()
