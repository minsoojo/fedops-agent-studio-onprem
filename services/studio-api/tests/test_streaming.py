import asyncio
import json
import threading
import time
import unittest

from studio_api.streaming import agent_chat_stream


class AgentChatStreamingTest(unittest.IsolatedAsyncioTestCase):
    async def test_closing_browser_stream_stops_blocking_generation(self):
        finished = threading.Event()

        def runner(on_delta):
            try:
                for index in range(1_000):
                    on_delta(str(index))
                    time.sleep(0.002)
                return {"response": "unexpected completion"}
            finally:
                finished.set()

        response = agent_chat_stream(runner)
        iterator = response.body_iterator
        stage = json.loads(await anext(iterator))
        delta = json.loads(await anext(iterator))

        self.assertEqual(stage["type"], "stage")
        self.assertEqual(delta["type"], "delta")
        await iterator.aclose()
        self.assertTrue(await asyncio.to_thread(finished.wait, 1.0))


if __name__ == "__main__":
    unittest.main()
