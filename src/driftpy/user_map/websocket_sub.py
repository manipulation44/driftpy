from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.accounts.ws.multi_account_subscriber import WebSocketProgramAccountSubscriber
from driftpy.memcmp import get_user_filter, get_non_idle_user_filter
from driftpy.accounts.types import UpdateCallback, WebsocketProgramAccountOptions
from driftpy.user_map.types import ConfigType

class WebsocketSubscription:
    def __init__(
            self, 
            user_map, 
            commitment, 
            on_update: UpdateCallback,
            skip_initial_load: bool = False,
            resub_timeout_ms: int = None,
            include_idle: bool = False,
        ):

        from driftpy.user_map.user_map import UserMap

        self.user_map: UserMap = user_map
        self.commitment = commitment
        self.on_update = on_update
        self.skip_initial_load = skip_initial_load
        self.resub_timeout_ms = resub_timeout_ms
        self.include_idle = include_idle
        self.subscriber = None

    async def subscribe(self):
        if not self.subscriber:
            filters = (get_user_filter(),)
            if not self.include_idle:
                filters += (get_non_idle_user_filter(),)
            options = WebsocketProgramAccountOptions(filters, self.commitment, "base64")
            self.subscriber = WebSocketProgramAccountSubscriber(self.user_map.drift_client.program, options, self.on_update)
        
        await self.subscriber.subscribe()

        if not self.skip_initial_load:
            await self.user_map.sync()
        
    async def unsubscribe(self):
        if not self.subscriber:
            return
        self.subscriber.unsubscribe()
        self.subscriber = None

    def get_subscription_config(self):
        return AccountSubscriptionConfig(ConfigType.WEBSOCKET.value, commitment = self.commitment)
