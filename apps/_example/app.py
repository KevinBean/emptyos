"""Hello World — example EmptyOS app."""

from emptyos.sdk import BaseApp, cli_command, on_event, web_route


class HelloApp(BaseApp):
    async def setup(self):
        await super().setup()
        # Track greeting count in persistent state
        self.state_data = self.load_state({"count": 0})

    @cli_command("hello", help="Say hello from EmptyOS")
    async def cmd_hello(self, name: str = "World"):
        self.state_data["count"] += 1
        self.save_state(self.state_data)
        await self.emit("hello:greeted", {"name": name, "count": self.state_data["count"]})
        self.print_rich(f"[bold green]Hello, {name}![/bold green] (greeting #{self.state_data['count']})")

    @web_route("GET", "/")
    async def page_index(self, request):
        return {
            "app": "hello",
            "message": f"Hello from EmptyOS! Greeted {self.state_data.get('count', 0)} times.",
        }

    @on_event("kernel:started")
    async def on_kernel_start(self, event):
        print("[HelloApp] Kernel started, hello app ready!")
