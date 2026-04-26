from emptyos.sdk import BaseApp, cli_command, web_route

class TestApp(BaseApp):

    @cli_command("run", help="Run the test app command")
    async def run(self):
        response = await self.think("Say hello from test app", domain="text")
        return response

    @web_route("GET", "/hello")
    async def hello(self, request):
        message = await self.think("Generate a friendly greeting for the web route", domain="text")
        return {"message": message}