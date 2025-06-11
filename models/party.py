from config import Config
from datetime import datetime
import asyncio
import json


class Party:
    TABLE = "parties"

    @classmethod
    async def create_party(cls, leader_id: str, leader_nickname: str):
        """Create a new party with the given leader"""
        loop = asyncio.get_running_loop()

        def db_insert():
            members = [
                {
                    "discord_id": leader_id,
                    "nickname": leader_nickname,
                    "role": "leader",
                    "joined_at": datetime.now().isoformat(),
                }
            ]
            return (
                Config.supabase()
                .table(cls.TABLE)
                .insert(
                    {
                        "leader_id": leader_id,
                        "members": json.dumps(members),
                        "created_at": datetime.now().isoformat(),
                    }
                )
                .execute()
            )

        return await loop.run_in_executor(None, db_insert)

    @classmethod
    async def find_user_party(cls, discord_id: str):
        """Find the party that a user is in"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = Config.supabase().table(cls.TABLE).select("*").execute()

            # Check each party's members to see if the user is in it
            for party in response.data:
                members = json.loads(party["members"])
                for member in members:
                    if member["discord_id"] == discord_id:
                        party["members"] = members  # Parse the JSON members
                        return party
            return None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def get_party(cls, party_id: int):
        """Get a party by ID"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("party_id", party_id)
                .execute()
            )
            if response.data:
                party = response.data[0]
                party["members"] = json.loads(party["members"])
                return party
            return None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def add_member(cls, party_id: int, discord_id: str, nickname: str):
        """Add a member to a party"""
        loop = asyncio.get_running_loop()

        def db_update():
            # First get the current party
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("party_id", party_id)
                .execute()
            )

            if not response.data:
                return None

            party = response.data[0]
            members = json.loads(party["members"])

            # Add new member
            new_member = {
                "discord_id": discord_id,
                "nickname": nickname,
                "role": "member",
                "joined_at": datetime.now().isoformat(),
            }
            members.append(new_member)

            # Update the party
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update({"members": json.dumps(members)})
                .eq("party_id", party_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def remove_member(cls, party_id: int, discord_id: str):
        """Remove a member from a party"""
        loop = asyncio.get_running_loop()

        def db_update():
            # First get the current party
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("party_id", party_id)
                .execute()
            )

            if not response.data:
                return None

            party = response.data[0]
            members = json.loads(party["members"])

            # Remove the member
            members = [
                member for member in members if member["discord_id"] != discord_id
            ]

            # If no members left, delete the party
            if not members:
                return (
                    Config.supabase()
                    .table(cls.TABLE)
                    .delete()
                    .eq("party_id", party_id)
                    .execute()
                )

            # Otherwise update the party
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update({"members": json.dumps(members)})
                .eq("party_id", party_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def delete_party(cls, party_id: int):
        """Delete a party"""
        loop = asyncio.get_running_loop()

        def db_delete():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .delete()
                .eq("party_id", party_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_delete)

    @classmethod
    async def get_member_count(cls, party_id: int):
        """Get the number of members in a party"""
        party = await cls.get_party(party_id)
        if party:
            return len(party["members"])
        return 0

    @classmethod
    async def is_leader(cls, party_id: int, discord_id: str):
        """Check if a user is the leader of a party"""
        party = await cls.get_party(party_id)
        if party:
            return party["leader_id"] == discord_id
        return False
