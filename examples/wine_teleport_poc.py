"""Wine / CrossOver proof of concept (Mac client).

Hooks the client, prints some state, then bounces the character a few units back and forth with
client.teleport(). Before the write freeze in MemoryReader.write_bytes, doing this a couple of
dozen times under Wine would eventually kill the game.

Run it with Windows Python inside the same Wine bottle as the game.

Controls:
  1      - Print zone, position and stats
  2      - Teleport 10 units forward and back, 25 times
  3      - Print whether wizwalker thinks it is running under Wine
  Ctrl+C - Unhook and exit
"""

import asyncio

from wizwalker import ClientHandler, XYZ, utils


async def ask(prompt):
    return await asyncio.get_event_loop().run_in_executor(None, lambda: input(prompt).strip())


async def main():
    handler = ClientHandler()
    clients = handler.get_new_clients()
    if not clients:
        print("No game clients found. Launch Wizard101 first.")
        return

    client = clients[0]

    try:
        print("Activating hooks...")
        await client.activate_hooks()

        print("\n=== Wine teleport PoC ===")
        print("  1      - Print state")
        print("  2      - Teleport back and forth")
        print("  3      - Wine check")
        print("  Ctrl+C - Exit\n")

        while True:
            choice = await ask("> ")

            if choice == "1":
                print(f"  zone: {await client.zone_name()}")
                print(f"  position: {await client.body.position()}")
                print(f"  health: {await client.stats.current_hitpoints()} / {await client.stats.max_hitpoints()}")

            elif choice == "2":
                start = await client.body.position()
                for i in range(25):
                    await client.teleport(XYZ(start.x, start.y + 10, start.z))
                    await asyncio.sleep(0.3)
                    await client.teleport(start)
                    await asyncio.sleep(0.3)
                print("  done, the game is still up")

            elif choice == "3":
                print(f"  is_wine: {utils.is_wine()}")

            else:
                print("  Unknown option. Use 1-3 or Ctrl+C.")

    except KeyboardInterrupt:
        print("\nCtrl+C received")
    finally:
        print("Unhooking...")
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
