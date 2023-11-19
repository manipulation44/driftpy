import asyncio
from typing import Mapping, Callable, List, Optional
from dataclasses import dataclass
import jsonrpcclient
from base64 import b64decode

from solana.rpc.commitment import Commitment
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey


@dataclass
class AccountToLoad:
    pubkey: Pubkey
    callback: Mapping[int, Callable[[bytes, int], None]]


@dataclass
class BufferAndSlot:
    slot: int
    buffer: Optional[bytes]


GET_MULTIPLE_ACCOUNTS_CHUNK_SIZE = 99


class BulkAccountLoader:
    def __init__(
        self,
        connection: AsyncClient,
        commitment: Commitment = "confirmed",
        frequency: int = 1,
    ):
        self.connection = connection
        self.commitment = commitment
        self.frequency = frequency
        self.task = None
        self.callback_id = 0
        self.accounts_to_load: Mapping[str, AccountToLoad] = {}
        self.buffer_and_slot_map: Mapping[str, BufferAndSlot] = {}

    def add_account(
        self, pubkey: Pubkey, callback: Callable[[bytes, int], None]
    ) -> int:
        existing_size = len(self.accounts_to_load)

        callback_id = self.callback_id

        pubkey_str = str(pubkey)
        existing_account_to_load = self.accounts_to_load.get(pubkey_str)
        if existing_account_to_load is not None:
            existing_account_to_load.callback[callback_id] = callback
        else:
            callbacks = {}
            callbacks[callback_id] = callback
            self.accounts_to_load[pubkey_str] = AccountToLoad(pubkey, callbacks)

        if existing_size == 0:
            self._start_loading()

        return callback_id

    def get_callback_id(self) -> int:
        self.callback_id += 1
        return self.callback_id

    def _start_loading(self):
        self.task = asyncio.create_task(self.load())

    def chunks(self, array: List, size: int) -> List[List]:
        return [array[i : i + size] for i in range(0, len(array), size)]

    async def load(self):
        while True:
            chunks = self.chunks(
                self.chunks(
                    list(self.accounts_to_load.values()),
                    GET_MULTIPLE_ACCOUNTS_CHUNK_SIZE,
                ),
                10,
            )

            await asyncio.gather(*[self.load_chunk(chunk) for chunk in chunks])
            await asyncio.sleep(self.frequency)

    async def load_chunk(self, chunk: List[List[AccountToLoad]]):
        if len(chunk) == 0:
            return

        rpc_requests = []
        for accounts_to_load in chunk:
            pubkeys_to_send = [
                str(accounts_to_load.pubkey) for accounts_to_load in accounts_to_load
            ]
            rpc_request = jsonrpcclient.request(
                "getMultipleAccounts",
                params=[
                    pubkeys_to_send,
                    {"encoding": "base64", "commitment": self.commitment},
                ],
            )
            rpc_requests.append(rpc_request)

        resp = await self.connection._provider.session.post(
            self.connection._provider.endpoint_uri,
            json=rpc_requests,
            headers={"content-encoding": "gzip"},
        )

        parsed_resp = jsonrpcclient.parse(resp.json())

        for rpc_result, chunk_accounts in zip(parsed_resp, chunk):
            if isinstance(rpc_result, jsonrpcclient.Error):
                print(f"Failed to get info about accounts: {rpc_result.message}")
                continue

            slot = rpc_result.result["context"]["slot"]

            for account_to_load in chunk_accounts:
                pubkey_str = str(account_to_load.pubkey)
                old_buffer_and_slot = self.buffer_and_slot_map.get(pubkey_str)

                if old_buffer_and_slot is not None and slot <= old_buffer_and_slot.slot:
                    continue

                new_buffer = None
                account = (
                    rpc_result.result["value"].pop(0)
                    if rpc_result.result["value"]
                    else None
                )
                if account:
                    new_buffer = b64decode(account["data"][0])

                if (
                    old_buffer_and_slot is None
                    or new_buffer != old_buffer_and_slot.buffer
                ):
                    self.handle_callbacks(account_to_load, new_buffer, slot)
                    self.buffer_and_slot_map[pubkey_str] = BufferAndSlot(
                        slot, new_buffer
                    )

    def handle_callbacks(
        self, account_to_load: AccountToLoad, buffer: Optional[bytes], slot: int
    ):
        for cb in account_to_load.callback.values():
            if bytes is not None:
                cb(buffer, slot)

    def unsubscribe(self):
        if self.task is not None:
            self.task.cancel()
            self.task = None
