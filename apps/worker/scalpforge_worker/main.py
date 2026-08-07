import asyncio
import logging


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.info("worker ready; connectors intentionally require explicit configuration")
    while True:
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(run())
