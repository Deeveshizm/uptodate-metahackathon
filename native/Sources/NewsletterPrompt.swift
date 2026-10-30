import Foundation

enum NewsletterPromptKind: String, CaseIterable, Identifiable {
    case recommendations, researchPapers, cooking, blogsAndArticles, other

    var id: String { rawValue }
    var title: String {
        switch self {
        case .recommendations: "Recommendations"
        case .researchPapers: "Research papers"
        case .cooking: "Cooking"
        case .blogsAndArticles: "Blogs & articles"
        case .other: "Other"
        }
    }
    var description: String {
        switch self {
        case .recommendations: "One recommendation per scheduled edition, with useful details and real photos when available."
        case .researchPapers: "Choose how papers are explained and which supporting links to include."
        case .cooking: "Recipes to cook, with ingredient lists, source-provided instructions, and real dish photos when available."
        case .blogsAndArticles: "Relevant reads with concise summaries, original article links, and source images."
        case .other: "A newsletter shaped around your interests and preferred kinds of posts."
        }
    }
    var nameHint: String {
        switch self {
        case .recommendations: "For example, My coffee pick"
        case .researchPapers: "For example, My AI research digest"
        case .cooking: "For example, What to cook this week"
        case .blogsAndArticles: "For example, My reading digest"
        case .other: "Give your newsletter a name"
        }
    }
    var topicHint: String {
        switch self {
        case .recommendations: "What to recommend and where, such as coffee shops in Tallahassee"
        case .researchPapers: "Research fields, questions, authors, or sources to follow"
        case .cooking: "Cuisines, dishes, or meal ideas you'd like to cook"
        case .blogsAndArticles: "Subjects, blogs, authors, or websites to follow"
        case .other: "Topics, interests, or sources to follow"
        }
    }
    var preferencesHint: String {
        switch self {
        case .recommendations: "Optional: atmosphere, budget, dietary needs, or what makes a good pick for you"
        case .researchPapers: "Optional: plain-language summaries, detailed methods, key results, or practical implications"
        case .cooking: "Optional: dietary preferences, ingredients, serving size, budget, or time to cook"
        case .blogsAndArticles: "Optional: practical takeaways, news, opinions, explainers, or preferred reading depth"
        case .other: "Optional: the kind of posts, detail, or style you'd like"
        }
    }
    var instructions: String {
        switch self {
        case .recommendations:
            """
            Include exactly one recommendation per scheduled edition, on my preferred delivery days and at the frequency above. Do not turn an edition into a list of multiple recommendations or send extra picks between scheduled editions. Choose a relevant, different pick when possible; if no suitable recommendation can be verified, say so rather than inventing one.

            Start with a natural description of the recommendation and why it fits my preferences. If it is a venue or establishment, include real pictures of that exact place when available, its full address and map link, official website, phone number, and opening hours by day, where those details can be verified. Embed or attach those photos if supported; otherwise include their original image or source-page links. Keep the photos and links with that single recommendation. Prefer current official venue information; if you include ratings or review summaries, name their source and distinguish them from your own recommendation. Omit unavailable details and never substitute unrelated photos or generated images for real venue pictures.
            """
        case .researchPapers:
            """
            Select substantial research papers relevant to my interests and requested technical depth. Explain naturally what each paper studies, how the method works, and what the findings show, including important caveats only when supported. Preserve the conditions and scope of reported results; do not invent performance claims or force every paper into identical headings.

            Include the paper's title, authors when available, and original paper link. Follow my supporting-link choices above, including only verified links when available; if official code is requested but none is found, say it was not found rather than inventing a repository. Treat each paper and all its related links as one story, not separate stories for the paper and its repository. Prefer relevant methodology figures or result charts from the paper itself, with their source links; never invent charts, results, or a repository that was not found.
            """
        case .cooking:
            """
            Find recipes I can cook that fit my cuisines, ingredients, dietary preferences, budget, and available time. Treat each dish and its source links as one story. Start with a short, natural description of the dish, its flavors, and why it fits my preferences.

            Include an ingredient list with the source's quantities and units, followed by numbered, step-by-step cooking instructions in the original order. Preserve serving size, preparation and cooking times, temperatures, and other important details when the recipe provides them. If the original instructions are written in paragraphs, turn them into ordered steps without adding actions or changing the method. Prefer recipes with complete instructions; never invent missing steps, measurements, timings, or substitutions.

            Include the original recipe link and real photos of that dish from the source when available, with their source links. Keep ingredients and instructions in the email body so I can read the recipe and build a grocery list from it. Use a cooking-friendly format rather than research-paper headings.
            """
        case .blogsAndArticles:
            """
            Find substantial blog posts and articles relevant to my topics, preferred authors, sources, and reading depth. For each article, write a self-contained, natural summary of its main point, useful details, and why it is relevant to my interests. Distinguish an author's opinion from established findings and preserve important qualifications. Use a structure that fits the article rather than fixed research-paper headings.

            Include the original article title, author, publication, and publication date when available, plus its canonical original URL. Keep the article and any directly related supporting links in one story. Include relevant real editorial images from that article when available, preferring an image that explains the content over a publisher logo. Put the useful summary in the email body rather than sending only a link or an attachment. Do not invent facts, quotes, publication details, or images.
            """
        case .other:
            """
            Build the newsletter around my topics and preferred kinds of posts. Write naturally and concisely at the depth I request, using a structure suited to the content rather than a fixed research-paper format. Separate substantial stories, keep related supporting links with the story they belong to, and include original source links and relevant real source images when available. Include only details supported by the sources; do not invent facts, ratings, instructions, links, or images.
            """
        }
    }

    var emailFields: String {
        switch self {
        case .recommendations:
            "For establishments, use the establishment's name as the recommendation's story heading; do not repeat it in a separate Venue or Name field. After the description, put these available details in separate labeled paragraphs: Address:, Maps:, Website:, Phone:, Rating:, Hours:. Include the rating's source. Write Hours on one line using semicolons between day/time pairs, such as Mon: [verified hours]; Tue: [verified hours]. Omit fields you cannot verify."
        case .researchPapers:
            "For each paper, use Original paper: for its main link. Label selected supporting links PDF:, Code:, Project page:, or Data: as appropriate, with a note that they belong to this paper. Include only the link types requested above and any explicitly requested extras."
        case .cooking:
            "Within each dish's story, use an Ingredients: label followed by a bullet list, and an Instructions: label followed by a numbered list. These are labels inside the story, not separate story headings. Put Original recipe: beside the canonical recipe link."
        case .blogsAndArticles:
            "For each article, label its canonical link Original article: and any related links Supporting links:. Keep those links and the article's images next to that article's summary."
        case .other:
            "Use Original source: for a story's main link and Supporting links: for its associated material. Keep useful details in readable paragraphs or lists within that same story."
        }
    }
}

enum NewsletterResearchLink: String, CaseIterable, Identifiable {
    case pdf, repository, projectPage, data

    var id: String { rawValue }
    var title: String {
        switch self {
        case .pdf: "PDF"
        case .repository: "Code / GitHub"
        case .projectPage: "Project page"
        case .data: "Data / supplementary material"
        }
    }
    var instruction: String {
        switch self {
        case .pdf: "Direct PDF link to the paper"
        case .repository: "The paper's official code or GitHub repository"
        case .projectPage: "The paper's official project page"
        case .data: "Official datasets or supplementary material associated with the paper"
        }
    }
}

enum NewsletterDeliveryFrequency: String, CaseIterable, Identifiable {
    case daily, weekly, fortnightly, monthly

    var id: String { rawValue }
    var title: String {
        switch self {
        case .daily: "Daily"
        case .weekly: "Weekly"
        case .fortnightly: "Every two weeks"
        case .monthly: "Monthly"
        }
    }
    var instruction: String {
        switch self {
        case .daily: "Once a day"
        case .weekly: "Once a week"
        case .fortnightly: "Once every two weeks"
        case .monthly: "Once a month"
        }
    }
}

struct NewsletterPromptDraft {
    static let emailFormat = """
    UpToDate parses these emails into readable posts. Make each edition self-contained so it does not need to discover the basic content on other pages. Send a normal rendered HTML email with one main body and one h2 heading per story, followed by that story's paragraphs, lists, links, and photos. Use inline labels, not extra headings, for ingredients, steps, contact details, or supporting links. If HTML email is unavailable, use readable plain text with the same labels; do not send raw HTML, JSON, code blocks, or an attachment-only edition.

    Use direct, canonical source URLs and clearly labeled links. Keep every supporting link with its story instead of making a separate links-only story or collecting unrelated sources at the bottom. For available real images, prefer an externally accessible HTTPS image URL in an img element, with a factual alt description and the image's source-page link. Put the most relevant content image first; avoid logos, tracking images, or unrelated stock photos. Do not invent image URLs or replace originals with generated pictures. If an image cannot be embedded, include its direct image URL and source link in that story.
    """
    var kind = NewsletterPromptKind.other
    var name = ""
    var topic = ""
    var postPreferences = ""
    var researchLinks: Set<NewsletterResearchLink> = [.pdf, .repository, .projectPage]
    var researchLinkNotes = ""
    var frequency = NewsletterDeliveryFrequency.daily
    var scheduleNotes = ""

    func prompt(alias: String, timezone: String) -> String? {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let topic = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        let alias = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !topic.isEmpty,
              alias.range(of: #"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$"#, options: .regularExpression) != nil else { return nil }

        let preferences = postPreferences.trimmingCharacters(in: .whitespacesAndNewlines)
        let schedule = scheduleNotes.trimmingCharacters(in: .whitespacesAndNewlines)
        var specifications = [
            "Newsletter name: \(name)",
            "Newsletter type: \(kind.title)",
            "Topics and interests: \(topic)",
        ]
        if !preferences.isEmpty {
            specifications.append("\(kind == .researchPapers ? "How I want papers covered" : "The kinds of posts I want"): \(preferences)")
        }
        if kind == .researchPapers {
            let links = NewsletterResearchLink.allCases.filter { researchLinks.contains($0) }.map(\.instruction)
            specifications.append("Supporting links to include when available: \(links.isEmpty ? "None requested beyond the original paper link" : links.joined(separator: "; "))")
            let notes = researchLinkNotes.trimmingCharacters(in: .whitespacesAndNewlines)
            if !notes.isEmpty { specifications.append("Other research-link preferences: \(notes)") }
        }
        specifications.append("Frequency: \(frequency.instruction)")
        specifications.append("Timezone: \(timezone)")
        if !schedule.isEmpty { specifications.append("Preferred delivery schedule: \(schedule)") }

        return """
        I want you to create a recurring newsletter for me and send each edition only to my UpToDate newsletter address: \(alias).

        The newsletter specifications are:
        \(specifications.joined(separator: "\n"))

        Keep the newsletter name as a consistent prefix in the email subject so UpToDate can recognize future editions. Put the newsletter content in the email body and follow my preferences above.

        \(kind.instructions)

        Email format for UpToDate:
        \(Self.emailFormat)

        \(kind.emailFields)

        Send editions to the UpToDate address above, not my personal inbox. Ask me to clarify any missing or conflicting scheduling details and obtain any required approval, then confirm when the recurring task has actually been created.
        """
    }
}
