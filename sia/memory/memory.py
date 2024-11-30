from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, asc, desc
from .models_db import SiaMessageModel, SiaCharacterSettingsModel, Base
from .schemas import SiaMessageSchema, SiaMessageGeneratedSchema, SiaCharacterSettingsSchema
from sia.character import SiaCharacter
import json

from utils.logging_utils import setup_logging, log_message, enable_logging

class SiaMemory:

    def __init__(self, db_path: str, character: SiaCharacter):
        self.db_path = db_path
        self.character = character
        self.engine = create_engine(self.db_path)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.logging_enabled = self.character.logging_enabled

        self.logger = setup_logging()
        enable_logging(self.logging_enabled)


    def add_message(self, message: SiaMessageGeneratedSchema, tweet_id: str = None, original_data: dict = None) -> SiaMessageSchema:
        session = self.Session()

        try:
            # Check if message with this ID already exists
            existing_message = session.query(SiaMessageModel).filter_by(id=tweet_id).first()
            if existing_message:
                log_message(self.logger, "info", self, f"Message with ID {tweet_id} already exists in database")
                return SiaMessageSchema.from_orm(existing_message)

            # Handle original_data serialization
            if original_data is not None:
                if isinstance(original_data, str):
                    try:
                        # Verify it's valid JSON if it's a string
                        json.loads(original_data)
                    except json.JSONDecodeError:
                        original_data = None
                elif isinstance(original_data, dict):
                    original_data = json.dumps(original_data)
                else:
                    original_data = None

            message_model = SiaMessageModel(
                id=tweet_id,
                character=message.character,
                platform=message.platform,
                author=message.author,
                content=message.content,
                conversation_id=message.conversation_id,
                response_to=message.response_to,
                flagged=message.flagged,
                message_metadata=message.message_metadata,
                original_data=original_data
            )

            session.add(message_model)
            session.commit()

            # Ensure original_data is parsed back to dict for schema
            if message_model.original_data and isinstance(message_model.original_data, str):
                message_model.original_data = json.loads(message_model.original_data)
            
            # Convert the model to a schema
            message_schema = SiaMessageSchema.from_orm(message_model)
            return message_schema
        
        except Exception as e:
            log_message(self.logger, "error", self, f"Error adding message to database: {e}")
            log_message(self.logger, "error", self, f"message type: {type(message)}")
            session.rollback()
            raise e
        
        finally:
            session.close()
        

    def get_messages(self, id=None, platform: str = None, author: str = None, not_author: str = None, character: str = None, conversation_id: str = None, flagged: bool = False, sort_by: bool = False, sort_order: str = "asc"):
        session = self.Session()
        try:
            query = session.query(SiaMessageModel)
            if id:
                query = query.filter_by(id=id)
            if character:
                query = query.filter_by(character=character)
            if platform:
                query = query.filter_by(platform=platform)
            if author:
                query = query.filter_by(author=author)
            if not_author:
                query = query.filter(SiaMessageModel.author != not_author)
            if conversation_id:
                query = query.filter_by(conversation_id=conversation_id)
            query = query.filter_by(flagged=flagged)
            if sort_by:
                if sort_order == "asc":
                    query = query.order_by(asc(sort_by))
                else:
                    query = query.order_by(desc(sort_by))
            posts = query.all()
            
            result = []
            for post in posts:
                # Create a copy of the post to modify
                post_dict = {
                    column.name: getattr(post, column.name)
                    for column in post.__table__.columns
                }
                
                # Parse original_data if it exists and is a string
                if post_dict.get('original_data'):
                    try:
                        if isinstance(post_dict['original_data'], str):
                            post_dict['original_data'] = json.loads(post_dict['original_data'])
                    except json.JSONDecodeError:
                        post_dict['original_data'] = None
                
                # Create schema from modified dictionary
                result.append(SiaMessageSchema.parse_obj(post_dict))
            
            return result
        finally:
            session.close()
    
    
    def clear_messages(self):
        session = self.Session()
        session.query(SiaMessageModel).filter_by(character=self.character.name).delete()
        session.commit()
        session.close()


    def reset_database(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)


    def get_character_settings(self):
        session = self.Session()
        try:
            character_settings = session.query(SiaCharacterSettingsModel).filter_by(character_name_id=self.character.name_id).first()
            if not character_settings:
                character_settings = SiaCharacterSettingsModel(
                    character_name_id=self.character.name_id,
                    character_settings={}
                )
                session.add(character_settings)
                session.commit()
            
            # Convert the SQLAlchemy model to a Pydantic schema before closing the session
            character_settings_schema = SiaCharacterSettingsSchema.from_orm(character_settings)
            return character_settings_schema

        finally:
            session.close()
    
        
    def update_character_settings(self, character_settings: SiaCharacterSettingsSchema):
        session = self.Session()
        # Convert the Pydantic schema to a dictionary
        character_settings_dict = character_settings.dict(exclude_unset=True)
        session.query(SiaCharacterSettingsModel).filter_by(character_name_id=self.character.name_id).update(character_settings_dict)
        session.commit()
        session.close()

    def has_processed_notification(self, notification_id: str) -> bool:
        session = self.Session()
        try:
            existing_message = session.query(SiaMessageModel).filter(
                (SiaMessageModel.id == notification_id) | 
                (SiaMessageModel.response_to == notification_id)
            ).first()
            return existing_message is not None
        finally:
            session.close()

    def add_processed_notification(self, notification_id: str, flagged: bool = False) -> SiaMessageSchema:
        session = self.Session()
        try:
            # Create a minimal record to track that we processed this notification
            message_model = SiaMessageModel(
                id=notification_id,
                platform="twitter",
                flagged=flagged,
                content="[PROCESSED_NOTIFICATION]",
                author="system",
                character=self.character.name
            )
            
            session.add(message_model)
            session.commit()
            
            return SiaMessageSchema.from_orm(message_model)
        
        except Exception as e:
            log_message(self.logger, "error", self, f"Error adding processed notification to database: {e}")
            session.rollback()
            raise e
        
        finally:
            session.close()
