// Display-layer cleanup of sentence-initial casing.
//
// The committed pipeline drafts occasionally start a sentence in lowercase
// ("a young woman sets down a stone bowl…"). The FIXTURE IS NOT MODIFIED —
// this is a presentation rule the demo applies when it loads a draft or
// re-renders one after a rename (documented in the work order). It uppercases
// the first alphabetic character of the text and of each following sentence;
// it never changes any other character.

export function sentenceCaseStart(text: string): string {
  let out = ''
  let atSentenceStart = true
  for (const ch of text) {
    if (atSentenceStart && /[a-zA-Z]/.test(ch)) {
      out += ch.toUpperCase()
      atSentenceStart = false
    } else {
      out += ch
      if (/[.!?]/.test(ch)) atSentenceStart = true
      // Digits consume the sentence start ("… 4 gaps" stays untouched);
      // quotes, dashes and whitespace pass it through to the first letter.
      else if (atSentenceStart && /[0-9]/.test(ch)) atSentenceStart = false
    }
  }
  return out
}
