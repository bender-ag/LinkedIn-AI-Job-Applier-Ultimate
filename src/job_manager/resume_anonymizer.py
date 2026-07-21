"""This module is used to anonymize the resume text and structured resume"""

import re
from typing import Any, Dict

from config.constants import DUMMY_PERSONAL_INFO_FEMALE, DUMMY_PERSONAL_INFO_MALE


class ResumeAnonymizer:
    def __init__(self, resume_structured: Dict[str, Any]):
        self.resume_anonymized = resume_structured.copy()
        self.github_links = []
        self.linkedin_links = []
        self.personal_information = resume_structured["personal_information"].copy()

    def anonymize_personal_information(self) -> None:
        """Anonymize personal information by replacing them with dummy data"""
        gender = self.resume_anonymized["personal_information"].get("gender")
        if gender.lower() == "female":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
        # anonymize the "personal information" fields
        for key, value in dummy_pesonal_info.items():
            if self.resume_anonymized["personal_information"].get(key):
                self.resume_anonymized["personal_information"][key] = value

    def anonymize_text(self, input_text: str) -> str:
        """If some key words are found in resume text - anonymize them"""
        gender = self.resume_anonymized["personal_information"].get("gender")
        output_text = input_text
        if gender.lower() == "female":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
        for key, value in dummy_pesonal_info.items():
            # anonymize the github username
            if key == "github":
                self.github_links = re.findall(
                    r"https?://(?:www\.)?github\.com/([^\s/]+)", output_text
                )
                self.github_links = [
                    f"https://github.com/{github_link}" for github_link in self.github_links
                ]
                if self.github_links:
                    output_text = re.sub(
                        r"(?:https?://)?(?:www\.)?github\.com/([^\s/]+)",
                        value,
                        output_text,
                    )
            elif key == "linkedin":
                self.linkedin_links = re.findall(
                    r"https?://(?:www\.)?linkedin\.com/in/([^\s/]+)", output_text
                )
                self.linkedin_links = [
                    f"https://linkedin.com/in/{linkedin_link}"
                    for linkedin_link in self.linkedin_links
                ]
                if self.linkedin_links:
                    output_text = re.sub(
                        r"(?:https?://)?(?:www\.)?linkedin\.com/in/([^\s/]+)",
                        value,
                        output_text,
                    )
            elif key in ["last_name_2"]:
                continue
            else:
                if not self.personal_information.get(key):
                    continue
                value_to_replace = self.personal_information[key]
                if "github" in value_to_replace:
                    continue
                if key in ["phone", "phone_code"]:
                    # Allow for optional spaces in phone numbers when replacing
                    # Remove all spaces from both the value_to_replace and input_ for matching, but preserve formatting in replacement
                    # Build a regex that matches the phone number with optional spaces between digits
                    phone_digits = re.sub(r"\s+", "", value_to_replace)
                    if phone_digits:
                        # Build regex: allow any number of spaces between digits
                        phone_pattern = r"".join([re.escape(d) + r"\s*" for d in phone_digits])
                        # Remove trailing \s* for the last digit
                        if phone_pattern.endswith(r"\s*"):
                            phone_pattern = phone_pattern[:-3]
                        output_text = re.sub(phone_pattern, value, output_text)
                else:
                    value_to_replace_escaped = re.escape(value_to_replace)
                    output_text = re.sub(rf"\b{value_to_replace_escaped}\b", value, output_text)
        return output_text

    @staticmethod
    def _link_repl(link: str):
        """Replacement callable that mirrors the matched text's scheme.

        A match that carried http(s):// (an href) gets the full link; a bare
        display-text match (e.g. "github.com/user") gets the link without the
        scheme, so visible text stays clean while hrefs stay clickable.
        """

        def repl(match: re.Match) -> str:
            if match.group(0).startswith("http"):
                return link
            return re.sub(r"^https?://(?:www\.)?", "", link)

        return repl

    def deanonymize_text(self, input_text: str) -> str:
        """Deanonymize the personal information in the input_text"""
        gender = self.resume_anonymized["personal_information"].get("gender")
        output_text = input_text
        if gender.lower() == "female":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
        for key, value_to_replace in dummy_pesonal_info.items():
            if key == "github":
                for i, github_link in enumerate(self.github_links):
                    output_text = re.sub(
                        r"(?:https?://)?(?:www\.)?github\.com/[^\"'<>\s/]+",
                        self._link_repl(github_link),
                        output_text,
                        count=0 if len(self.github_links) == 1 else i + 1,
                    )
            elif key == "linkedin":
                for i, linkedin_link in enumerate(self.linkedin_links):
                    output_text = re.sub(
                        r"(?:https?://)?(?:www\.)?linkedin\.com/in/[^\"'<>\s/]+",
                        self._link_repl(linkedin_link),
                        output_text,
                        count=0 if len(self.linkedin_links) == 1 else i + 1,
                    )
            else:
                if "github" in value_to_replace or "linkedin" in value_to_replace:
                    continue
                # LLM sometimes hallucinates and gives the wrong last name
                # this code is added to fix this bug
                if key in ["last_name_2"]:
                    key_ = "last_name"
                else:
                    key_ = key
                if not self.personal_information.get(key_):
                    continue
                value = self.personal_information[key_]
                value_to_replace_escaped = re.escape(value_to_replace)
                if key_ in ["phone", "phone_code"]:
                    output_text = re.sub(rf"{value_to_replace_escaped}", value, output_text)
                else:
                    output_text = re.sub(rf"\b{value_to_replace_escaped}\b", value, output_text)
        return output_text


if __name__ == "__main__":
    resume_text = """Hello, my name is John Doe and my phone number is 9991234567 and my phone code is +7.
    My full phone number is +79991234567.
    My email is john.doe@example.com. My LinkedIn is https://linkedin.com/in/johndoe.
    My GitHub is https://github.com/johndoe. My zip code is 123456.
    My address is 322 Fleet Street. My city is Galway. My country is Ireland.
    My date of birth is 01.01.1990.
    My gender is male."""
    resume_structured = {
        "personal_information": {
            "name": "John Doe",
            "phone": "9991234567",
            "phone_code": "+7",
            "email": "john.doe@example.com",
            "linkedin": "https://linkedin.com/in/johndoe",
            "github": "https://github.com/johndoe",
            "zip_code": "123456",
            "address": "322 Fleet Street",
            "city": "Galway",
            "country": "Ireland",
            "date_of_birth": "01.01.1990",
            "gender": "male",
        },
    }
    resume_anonymizer = ResumeAnonymizer(resume_structured)
    resume_anonymizer.anonymize_personal_information()
    anonymized_resume_text = resume_anonymizer.anonymize_text(resume_text)
    deanonymized_resume_text = resume_anonymizer.deanonymize_text(anonymized_resume_text)

    print(f"Anonymized resume text: {anonymized_resume_text}")
    print(f"Deanonymized resume text: {deanonymized_resume_text}")
