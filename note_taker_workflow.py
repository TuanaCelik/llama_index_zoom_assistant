import os
import json
import hmac
import hashlib
import asyncio
import websockets
import ssl

import requests
from datetime import datetime
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List, Literal

from llama_index.core.workflow import StartEvent, StopEvent, Workflow, step, Event, Context
from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI


load_dotenv()
NOTION_SECRET_TOKEN = os.getenv("NOTION_SECRET_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
ZOOM_SECRET_TOKEN = os.getenv("ZOOM_SECRET_TOKEN")
CLIENT_ID = os.getenv("ZM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZM_CLIENT_SECRET")


headers = {
    "Authorization": "Bearer " + NOTION_SECRET_TOKEN,
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",  # Check what is the latest version here: https://developers.notion.com/reference/changes-by-version
}

class CreateNotionPage(Event):
    title: str

class CheckForActions(Event):
    transcript_chunk: List[str]

class SummarizeMeeting(Event):
    transcript: str

class ActionItems(Event):
    to_dos: List[str]

class Meeting(BaseModel):
    summary: str = Field(description="The summary of the meeting")
    attendees: List[str] = Field(description="The list of attendees of the meeting")

class Action(BaseModel):
    action: Literal["do_nothing", "create_action_items"] = Field(description="Whether the transcript suggests that there's a new action item to be created or not.")
    action_items: List[str] = Field(description="List of precise action items. Empty if there are none.",  default=[])

class NoteTakerWorkflow(Workflow):
    active_connections = {}
    full_transcript = ""
    page_id = ""
    meeting_uuid = None

    def generate_signature(self, client_id, meeting_uuid, stream_id, client_secret):
        message = f"{client_id},{meeting_uuid},{stream_id}"
        return hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    async def handle_signaling_connection(self, meeting_uuid, stream_id, server_url, transcript_chunk, ctx: Context):
        print("Connecting to signaling WebSocket for meeting", meeting_uuid)
        try:
            async with websockets.connect(server_url) as ws:
                if meeting_uuid not in self.active_connections:
                    self.active_connections[meeting_uuid] = {}
                    ctx.send_event(CreateNotionPage(title=f"Zoom Meeting {datetime.today().strftime('%Y-%m-%d')}"))
                self.active_connections[meeting_uuid]["signaling"] = ws

                # Step 2: Send SIGNALING_HAND_SHAKE_REQ
                signature = self.generate_signature(CLIENT_ID, meeting_uuid, stream_id, CLIENT_SECRET)
                handshake = {
                    "msg_type": 1,  # SIGNALING_HAND_SHAKE_REQ
                    "protocol_version": 1,
                    "meeting_uuid": meeting_uuid,
                    "rtms_stream_id": stream_id,
                    "sequence": int(asyncio.get_event_loop().time() * 1e9),
                    "signature": signature
                }
                await ws.send(json.dumps(handshake))
                print("Sent signaling handshake (SIGNALING_HAND_SHAKE_REQ)")

                while True:
                    try:
                        data = await ws.recv()
                        msg = json.loads(data)
                    
                        # Handle SIGNALING_HAND_SHAKE_RESP
                        if msg["msg_type"] == 2 and msg["status_code"] == 0:
                            media_urls = msg.get("media_server", {}).get("server_urls", {})
                            media_url = media_urls.get("transcript") or media_urls.get("all")
                            if media_url:
                                handler = asyncio.create_task(self.handle_media_connection(media_url, meeting_uuid, stream_id, ws, transcript_chunk, ctx))

                        # Handle KEEP_ALIVE
                        if msg["msg_type"] == 12:  # KEEP_ALIVE_REQ
                            await ws.send(json.dumps({
                                "msg_type": 13,  # KEEP_ALIVE_RESP
                                "timestamp": msg["timestamp"]
                            }))
                            print("Responded to signaling keep-alive")

                        # Step 11: Handle STREAM_STATE_UPDATE (TERMINATED)
                        if msg["msg_type"] == 7 and msg.get("state") == 4:
                            print("Received STREAM_STATE_UPDATE (TERMINATED)")
                            break

                    except websockets.exceptions.ConnectionClosed:
                        break
                    except Exception as e:
                        print("Error processing signaling message:", e)
                        break
        except Exception as e:
            print("Signaling socket error:", e)
        finally:
            print("Signaling socket closed")
            if meeting_uuid in self.active_connections:
                self.active_connections[meeting_uuid].pop("signaling", None)

    async def handle_media_connection(self, media_url, meeting_uuid, stream_id, signaling_socket, transcript_chunk, ctx: Context):
        print("Connecting to media WebSocket at", media_url)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        try:
            async with websockets.connect(media_url, ssl=ssl_context) as media_ws:
                if meeting_uuid in self.active_connections:
                    self.active_connections[meeting_uuid]["media"] = media_ws

                # Send DATA_HAND_SHAKE_REQ
                signature = self.generate_signature(CLIENT_ID, meeting_uuid, stream_id, CLIENT_SECRET)
                handshake = {
                    "msg_type": 3,  # DATA_HAND_SHAKE_REQ
                    "protocol_version": 1,
                    "meeting_uuid": meeting_uuid,
                    "rtms_stream_id": stream_id,
                    "signature": signature,
                    "media_type": 8,  # TRANSCRIPT
                    "payload_encryption": False
                }
                await media_ws.send(json.dumps(handshake))
                
                while True:
                    try:
                        data = await media_ws.recv()
                        try:
                            msg = json.loads(data)
                            # Handle DATA_HAND_SHAKE_RESP
                            if msg["msg_type"] == 4 and msg["status_code"] == 0:
                                # Send STREAM_STATE_UPDATE (ACTIVE)
                                await signaling_socket.send(json.dumps({
                                    "msg_type": 7,  # STREAM_STATE_UPDATE
                                    "rtms_stream_id": stream_id
                                }))
                                print("Media handshake success, sent STREAM_STATE_UPDATE")

                            # Handle KEEP_ALIVE
                            if msg["msg_type"] == 12:  # KEEP_ALIVE_REQ
                                await media_ws.send(json.dumps({
                                    "msg_type": 13,  # KEEP_ALIVE_RESP
                                    "timestamp": msg["timestamp"]
                                }))
                                print("Responded to media keep-alive")

                            # Handle MEDIA_DATA_TRANSCRIPT
                            if msg.get("msg_type") == 17:  # MEDIA_DATA_TRANSCRIPT
                                transcript_text = msg["content"].get("data", "")
                                user_name = msg["content"].get("user_name", "Unknown User")
                                if transcript_text:
                                    self.full_transcript += f"\n {user_name}: {transcript_text}"
                                    if len(transcript_chunk) < 5:
                                        transcript_chunk.append(f"{user_name}: {transcript_text}")
                                    else:
                                        ctx.send_event(CheckForActions(transcript_chunk=transcript_chunk))
                                        transcript_chunk = [f"{user_name}: {transcript_text}"]
                        except json.JSONDecodeError:
                            print("Non-JSON media data received")
                    except websockets.exceptions.ConnectionClosed:
                        break
                    except Exception as e:
                        print("Error receiving media message:", e)
                        break
        except Exception as e:
            print("Media socket error:", e)
        finally:
            print("Media socket closed")
            if meeting_uuid in self.active_connections:
                self.active_connections[meeting_uuid].pop("media", None)

    @step
    async def start_note_taker(self, ev: StartEvent, ctx: Context) -> CreateNotionPage | SummarizeMeeting | CheckForActions:
        zoom_event = ev.body.get("event")
        payload = ev.body.get("payload", {})

        # URL validation challenge
        if zoom_event == "endpoint.url_validation" and payload.get("plainToken"):
            hash_obj = hmac.new(
                ZOOM_SECRET_TOKEN.encode(),
                payload["plainToken"].encode(),
                hashlib.sha256
            )
            print("Responding to URL validation challenge")
            return {
                "plainToken": payload["plainToken"],
                "encryptedToken": hash_obj.hexdigest()
            }

        #  Handle meeting.rtms_started - Triggers the WebSocket connection flow
        if zoom_event == "meeting.rtms_started":
            transcript_chunk = []
            print("RTMS Started event received")
            meeting_uuid = payload.get("meeting_uuid")
            rtms_stream_id = payload.get("rtms_stream_id")
            server_urls = payload.get("server_urls")
            # print("Whole payload: ", payload)
            if all([meeting_uuid, rtms_stream_id, server_urls]):
                handler = asyncio.create_task(
                                self.handle_signaling_connection(meeting_uuid, rtms_stream_id, server_urls, transcript_chunk, ctx)
                            )
        # Handle meeting.rtms_stopped
        if zoom_event == "meeting.rtms_stopped":
            meeting_uuid = payload.get("meeting_uuid")
            if meeting_uuid in self.active_connections:
                connections = self.active_connections[meeting_uuid]
                for conn in connections.values():
                    if conn and hasattr(conn, "close"):
                        await conn.close()
                self.active_connections.pop(meeting_uuid, None)
            return SummarizeMeeting(transcript=self.full_transcript)

    @step
    async def create_notion_page(self, ev: CreateNotionPage, ctx: Context) -> None:
        page = {"Name": {
                    "id": "title",
                    "type": "title",
                    "title": [{
                        "type": "text",
                        "text": {
                            "content": ev.title,
                            "link": None
                        },  
                        "annotations": {
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default"
                        },
                        "plain_text": ev.title,
                        "href": None
                        }]
                        }
                    }

        create_url = "https://api.notion.com/v1/pages"
        payload = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": page}
        res = requests.post(create_url, headers=headers, json=payload)
        self.page_id = res.json()["id"]
        if res.status_code == 200:
            print(f"{res.status_code}: Page created successfully")
        else:
            print(f"{res.status_code}: Error during page creation")
        return None
        
    @step
    async def evaluate_actions(self, ev: CheckForActions, ctx: Context) -> None | ActionItems:
        llm = OpenAI(model="gpt-4.1-mini")
        system_prompt = """You evaluate whether there are any reasonable action items that can be noted from a short transcript chunk from a zoom meeting.
        For example, if it's just a chat, you can select 'do_nothing', but if there are clear discussions on what to do, you can select 'create_action_item"""
        print("Here's the chunk: \n", str(ev.transcript_chunk))
        sllm = llm.as_structured_llm(Action)
        response = await sllm.achat([ChatMessage(role="system", content=system_prompt), ChatMessage(role="user", content=str(ev.transcript_chunk))])
        
        if response.raw.action == "create_action_items":
            return ActionItems(to_dos=response.raw.action_items)
        return None
    
    @step
    async def add_action_items(self, ev: ActionItems, ctx: Context) -> None:
        blocks = {
            "children": [
                {
                    "object": "block",
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": to_do,
                                },
                            }
                        ]
                    },
                } for to_do in ev.to_dos
            ]
        }
        edit_url = f"https://api.notion.com/v1/blocks/{self.page_id}/children"
        res = requests.patch(edit_url, headers=headers, json=blocks)
        if res.status_code == 200:
            print(f"{res.status_code}: Page edited successfully")
        else:
            print(f"{res.status_code}: Error during page editing")
        return None
    
    @step
    async def meeting_end_summary(self, ev: SummarizeMeeting, ctx: Context) -> StopEvent:
        llm = OpenAI(model="gpt-4.1-mini")
        system_prompt = """You're a meeting summarizer. Based on the full transcript provided by the uesr.
        Summarize the meeting, mentioning opinions highlighted by the participants. You should also create a
        full list of attendees. Provide the summary as a proper markdown block for a Notion bage, with relevant 
        headers etc."""

        sllm = llm.as_structured_llm(Meeting)
        response = await sllm.achat([ChatMessage(role="system", content=system_prompt), ChatMessage(role="user", content=ev.transcript)])

        blocks = {
            "children": [
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": "Meeting Summary"
                                },
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": response.raw.summary,
                                },
                            }
                        ]
                    },
                }
            ]
        }

        edit_url = f"https://api.notion.com/v1/blocks/{self.page_id}/children"
        res = requests.patch(edit_url, headers=headers, json=blocks)
        if res.status_code == 200:
            print(f"{res.status_code}: Page edited successfully")
        else:
            print(f"{res.status_code}: Error during page editing")
        return StopEvent(result="Meeting ended and summary created")