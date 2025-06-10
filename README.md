# Zoom Assistant for Notion built with LlamaIndex

This example demonstrates how to receive and process incoming transcript data from a Zoom meeting using the RTMS (Real-Time Media Streaming) service, combined with LlamaIndex, the Notion API and OpenAI to dp a few things: 
1. Create a new Meeting Notes page in a Notion database when the meeting starts,
2. Automatically extract action items from the conversation as the conversation is ongoing
3. When the meeting ends, create a meeting summary.

<figure class="video_container">
  <iframe src="/assets/example.mp4" frameborder="0" allowfullscreen="true"> 
</iframe>
</figure>

![](/assets/workflow.png)

## Prerequisites

- Python 3.7 or higher
- A Zoom account with RTMS enabled
- Zoom App credentials (Client ID and Client Secret)
- Zoom Secret Token for webhook validation
- OpenAI API key for Claude access
- A Notion databse with "Name", "Created at" and "Attendees" properties (see image below)
- Notion Secret Key and Database ID

![](/assets/database.png)
## Setup

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file in the same directory with your credentials:
```
# Zoom App Credentials
ZOOM_SECRET_TOKEN=your_zoom_secret_token
ZM_CLIENT_ID=your_zoom_client_id
ZM_CLIENT_SECRET=your_zoom_client_secret
NOTION_SECRET_TOKEN=your_notion_secret_token
NOTION_DATABASE_ID=your_notion_database_id
OPENAI_API_KEY=your_openai_api_key
```

## Running the Example

1. Start the server:
```bash
python note_taker.py
```

2. The server will start on port 3000. You'll need to expose this port to the internet using a tool like ngrok:
```bash
ngrok http 3000
```

3. Configure your Zoom App's webhook URL to point to your exposed endpoint (e.g., `https://your-ngrok-url/webhook`)

4. Start a Zoom meeting and enable RTMS. The server will:
   - Receive and process the incoming transcript data
   - Extract action items using Claude
   - Print both the raw transcripts and identified action items to the console

## How it Works

The application is built as a real-time meeting assistant that processes Zoom meeting transcripts and creates structured Notion pages. Here's how it works:

### 1. Zoom Integration (`note_taker.py`)
- Acts as a webhook server that receives Zoom RTMS (Real-Time Media Streaming) events
- Handles the initial webhook validation and meeting lifecycle events
- Manages WebSocket connections to Zoom's signaling and media servers
- Processes real-time transcript data as it streams from the meeting

### 2. Meeting Processing Workflow (`note_taker_workflow.py`)
- Creates a new Notion page at the start of each meeting
- Processes transcript chunks in real-time using a sliding window approach
- Uses GPT-4 to analyze conversation segments for action items
- Automatically adds identified action items to the Notion page as to-do items
- Generates a comprehensive meeting summary when the meeting ends, including:
  - A detailed summary of the discussion
  - List of meeting attendees
  - All identified action items

The workflow maintains context by:
- Keeping track of the full meeting transcript
- Processing transcript chunks in small batches (5 messages at a time)
- Using structured LLM calls to identify action items
- Automatically formatting and organizing content in Notion

## Future Improvements 💚 (open to contributions)

A few things on my wish-list which I have not implemented:
- Get list of attendees from Zoom RTMS and add it to the `Atendees` property
- Integrate with email or slack to deal with attendee instructions such as "Bart is missing from this call, send him a reminder please" 