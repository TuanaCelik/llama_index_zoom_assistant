import uvicorn
from fastapi import FastAPI, Request
from note_taker_workflow import NoteTakerWorkflow


app = FastAPI()
port = 3000

workflow = NoteTakerWorkflow(timeout=None)
active_connections = {}

@app.post("/webhook")
async def webhook(request: Request):
    """
    Step 1: Handle meeting.rtms_started - Initial entry point
    Step 10: Handle meeting.rtms_stopped - Cleanup
    """
    try:
        body = await request.json()
        await workflow.run(body=body)
    except Exception as e:
        print("Failed to parse JSON body:", e)
        body = {}
    return {"status": "ok"}

if __name__ == "__main__":
    print(f"Server running at http://localhost:{port}")
    print(f"Webhook endpoint available at http://localhost:{port}/webhook")
    uvicorn.run(app, host="0.0.0.0", port=port)
