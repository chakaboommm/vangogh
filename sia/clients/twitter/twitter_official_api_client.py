import os
import json
import time

import tweepy
from tweepy import Tweet, Forbidden

from sia.clients.client import SiaClient
from sia.memory.schemas import SiaMessageGeneratedSchema, SiaMessageSchema
from sia.memory.memory import SiaMemory
from sia.character import SiaCharacter

from utils.logging_utils import setup_logging, log_message, enable_logging

class SiaTwitterOfficial(SiaClient):
    
    def __init__(self, api_key, api_secret_key, access_token, access_token_secret, bearer_token, character: SiaCharacter = None, memory: SiaMemory = None, logging_enabled=True):
        super().__init__(
            client=tweepy.Client(
                consumer_key=api_key, consumer_secret=api_secret_key,
                access_token=access_token, access_token_secret=access_token_secret,
                bearer_token=bearer_token
            )
        )

        self.logger = setup_logging()
        enable_logging(logging_enabled)

        self.api_key = api_key
        self.api_secret_key = api_secret_key
        self.access_token = access_token
        self.access_token_secret = access_token_secret
        self.memory = memory
        self.character = character


    def publish_post(self, post:SiaMessageGeneratedSchema, media:dict=[], in_reply_to_tweet_id:str=None) -> str:
        
        media_ids = None
        if media:
            media_ids = []
            for m in media:
                media_ids.append(
                    self.upload_media(m)
                )
        
        try:
            print(f"post: {post}")
            print(f"media_ids: {media_ids}")
            print(f"in_reply_to_tweet_id: {in_reply_to_tweet_id}")
            
            response = self.client.create_tweet(
                text=post.content,
                **({"media_ids": media_ids} if media_ids else {}),
                in_reply_to_tweet_id=in_reply_to_tweet_id
                # **({"in_reply_to_tweet_id": in_reply_to_tweet_id} if in_reply_to_tweet_id else {})
            )
            print(f"Tweet sent successfully!: {response}")
            return response.data['id']
        except Forbidden as e:
            print(f"Failed to send tweet: {e}")
            return Forbidden
        except Exception as e:
            print(f"Failed to send tweet: {e}")


    def upload_media(self, media_filepath):
        auth = tweepy.OAuth1UserHandler(self.api_key, self.api_secret_key)
        auth.set_access_token(
            self.access_token,
            self.access_token_secret,
        )
        client_v1 = tweepy.API(auth)

        media = client_v1.media_upload(filename=media_filepath)
        
        return media.media_id


    def get_my_tweet_ids(self):
        my_tweets = self.memory.get_messages(platform="twitter", author=self.character.name)
        return [tweet.id for tweet in my_tweets]
    
    
    def get_last_retrieved_reply_id(self):
        replies = self.memory.get_messages(platform="twitter", not_author=self.character.name)
        if replies:
            return max(replies, key=lambda reply: reply.id).id


    def get_new_replies_to_my_tweets(self) -> list[SiaMessageSchema]:
        since_id = self.get_last_retrieved_reply_id()

        # sorted to process newer tweets first
        my_tweet_ids = sorted(self.get_my_tweet_ids(), key=int, reverse=True)

        log_message(self.logger, "info", self, f"since_id: {since_id}")
        log_message(self.logger, "info", self, f"my_tweet_ids: {my_tweet_ids}")

        messages = []

        for i in range(0, len(my_tweet_ids), 10):
            time.sleep(20)
            batch = my_tweet_ids[i:i+10]
            query = " OR ".join([f"conversation_id:{id}" for id in batch])
            log_message(self.logger, "info", self, f"query: {query}")

            try:
                new_replies_to_my_tweets = self.client.search_recent_tweets(
                    query=query,
                    since_id=since_id,
                    tweet_fields=["conversation_id","created_at","in_reply_to_user_id"],
                    expansions=["author_id","referenced_tweets.id"]
                )
            except Exception as e:
                log_message(self.logger, "error", self, f"Error getting replies: {e}")
                continue
            
            if not new_replies_to_my_tweets.data:
                continue
            
            for reply in new_replies_to_my_tweets.data:
                
                # exclude replies from the character itself
                author = next((user.username for user in new_replies_to_my_tweets.includes['users'] if user.id == reply.author_id), None)
                log_message(self.logger, "info", self, f"author of the received reply: {author}")
                if author == self.character.twitter_username:
                    continue
                
                try:
                    from openai import OpenAI
                    client = OpenAI()
                    moderation_response = client.moderations.create(
                        model="omni-moderation-latest",
                        input=reply.text,
                    )
                    flagged = moderation_response.results[0].flagged
                    if flagged:
                        log_message(self.logger, "info", self, f"flagged reply: {reply.text}")
                except Exception as e:
                    log_message(self.logger, "error", self, f"Error moderating reply: {e}")
                    flagged = False
                
                try:
                    message = self.memory.add_message(
                        SiaMessageGeneratedSchema(
                            conversation_id=str(reply.data['conversation_id']),
                            content=reply.text,
                            platform="twitter",
                            author=next(user.username for user in new_replies_to_my_tweets.includes['users'] if user.id == reply.author_id),
                            response_to=str(next((ref.id for ref in reply.referenced_tweets if ref.type == "replied_to"), None)) if reply.referenced_tweets else None,
                            wen_posted=reply.created_at,
                            flagged=int(flagged),
                            metadata=moderation_response
                        ),
                        tweet_id=str(reply.id),
                        original_data=reply.data
                    )
                    messages.append(message)
                except Exception as e:
                    log_message(self.logger, "error", self, f"Error adding message: {e}")

        return messages


    def get_conversation(self, conversation_id: str) -> list[SiaMessageSchema]:
        messages = self.memory.get_messages(conversation_id=conversation_id, sort_by="wen_posted", sort_order="asc", flagged=False)
        return messages


    def get_notifications(self, since_id=None) -> list[SiaMessageSchema]:
        """
        Fetch notifications (mentions) for the authenticated user
        
        Args:
            since_id (str, optional): Only return notifications after this ID
            
        Returns:
            list[SiaMessageSchema]: List of messages from notifications
        """
        messages = []
        try:
            mentions = self.client.get_users_mentions(
                id=self.client.get_me().data.id,  # Get authenticated user's ID
                since_id=since_id,
                tweet_fields=["conversation_id", "created_at", "in_reply_to_user_id"],
                expansions=["author_id", "referenced_tweets.id"]
            )
            
            if not mentions.data:
                return messages
                
            for mention in mentions.data:
                # Skip if we've already processed this notification
                if self.memory.has_processed_notification(str(mention.id)):
                    log_message(self.logger, "info", self, f"Skipping already processed notification: {mention.id}")
                    continue

                # Skip mentions from the character itself
                author = next((user.username for user in mentions.includes['users'] 
                             if user.id == mention.author_id), None)
                if author == self.character.twitter_username:
                    self.memory.add_processed_notification(str(mention.id))  # Mark self-mentions as processed
                    continue
                    
                # Moderate content
                try:
                    from openai import OpenAI
                    client = OpenAI()
                    moderation_response = client.moderations.create(
                        model="omni-moderation-latest",
                        input=mention.text,
                    )
                    flagged = moderation_response.results[0].flagged
                    if flagged:
                        self.memory.add_processed_notification(str(mention.id), flagged=True)
                        continue
                except Exception as e:
                    log_message(self.logger, "error", self, f"Error moderating mention: {e}")
                    flagged = False
                    moderation_response = None
                
                try:
                    message = self.memory.add_message(
                        SiaMessageGeneratedSchema(
                            conversation_id=str(mention.data['conversation_id']),
                            content=mention.text,
                            platform="twitter",
                            author=author,
                            response_to=str(next((ref.id for ref in mention.referenced_tweets if ref.type == "replied_to"), None)) if mention.referenced_tweets else None,
                            wen_posted=mention.created_at,
                            flagged=int(flagged),
                            metadata=moderation_response
                        ),
                        tweet_id=str(mention.id),
                        original_data=mention.data
                    )
                    messages.append(message)
                except Exception as e:
                    log_message(self.logger, "error", self, f"Error adding mention to memory: {e}")
                    # Still mark as processed even if we failed to add it
                    self.memory.add_processed_notification(str(mention.id))
                    
        except Exception as e:
            log_message(self.logger, "error", self, f"Error getting notifications: {e}")
            
        return messages
