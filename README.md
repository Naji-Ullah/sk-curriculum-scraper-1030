# Saskatchewan Curriculum Scraper - Levels 10, 20, 30

Scrapes curriculum data (outcomes, indicators, PDF links) from [curriculum.gov.sk.ca](https://curriculum.gov.sk.ca/) for all Level 10, 20, and 30 courses.

## Subjects Covered

- **Arts Education**: Arts Education, Band, Choral, Dance, Drama, Guitar, Instrumental Jazz, Music, Studio Art, Visual Art, Vocal Jazz
- **Languages**: Core French, Dakota, Ukrainian, German, Mandarin, Spanish, Dene, Michif, Michif French, Nakawē, Nakoda, nēhiyawēwin, American Sign Language
- **English Language Arts**: ELA 10/20/30, Creative Writing, Journalism Studies, Media Studies
- **Financial Literacy**: Financial Literacy 10
- **Physical Education & Wellness**: Wellness 10, PE 20, PE 30
- **Sciences**: Biology, Chemistry, Computer Science, Earth Science, Environmental Science, Health Science, Physical Science, Physics, Science 10
- **Mathematics**: Foundations of Math, Pre-Calculus, Workplace & Apprenticeship Math, Calculus
- **Social Studies**: Geography, History, Native Studies, Economics, Psychology, Law, Social Studies
- **Additional**: Catholic Studies, Christian Ethics

## Running Locally

```bash
pip install -e .
uvicorn app.main:app --reload
```

## JSON Output Format

Each subject produces a JSON file like:

```json
{
    "Subject Name": {
        "Pdf_url": "https://curriculum.gov.sk.ca/CurriculumFile?id=...",
        "Level 10": {
            "Outcomes": [
                {
                    "code": "XX10.1",
                    "title": "Outcome description...",
                    "indicators": [
                        "(a) First indicator...",
                        "(b) Second indicator..."
                    ]
                }
            ]
        },
        "Level 20": { ... },
        "Level 30": { ... }
    }
}
```

## Deployment

Deployed on Fly.io. Visit the web UI to trigger scraping and download results.
