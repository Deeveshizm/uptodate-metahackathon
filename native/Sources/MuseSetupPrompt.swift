import Foundation

enum MuseSetupPrompt {
    static func make(schema: URL, newKeyIssued: Bool = false) -> String? {
        guard schema.scheme == "https", schema.host != nil, schema.user == nil,
              schema.password == nil, schema.query == nil, schema.fragment == nil else { return nil }
        let connectionInstruction = newKeyIssued
            ? "Connect my UpToDate account for direct newsletter delivery. I have just created a new connection key in UpToDate. Any earlier UpToDate key has been invalidated. Reuse the existing UpToDate custom connector configuration, but update its saved credential through your secure credential setup with the latest key I provide there. Do not keep retrying the previous credential or create a duplicate connector. If no UpToDate connector exists, set one up using the specification below."
            : "Connect my UpToDate account for direct newsletter delivery. First reuse any existing UpToDate custom connector and test it with listCustomNewsletters. If that succeeds, keep its stored credential and confirm the connection; do not ask me for the key again."
        return """
        \(connectionInstruction)

        If UpToDate is not configured yet, create a personal custom connector using this OpenAPI specification: \(schema.absoluteString)

        Request my account's UpToDate connection key only through your secure credential setup if it is needed. I will provide it there separately; never ask me to paste it into a chat message. Do not generate or replace a connection key yourself. If an existing credential fails, explain the actual authentication error before requesting an updated key.

        After saving the credential, verify access using the read-only listCustomNewsletters tool. An empty newsletter list is a successful connection. Confirm only after the API accepts that request. Do not create a newsletter, send email or schedule anything during connection setup; I will make a separate newsletter request afterward.
        """
    }
}
