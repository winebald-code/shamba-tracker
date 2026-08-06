Montserrat, SIL Open Font License 1.1, from github.com/google/fonts.

pdf_gen.font_css() embeds this file as base64 into every generated PDF, so
WeasyPrint renders the report in the brand face instead of substituting DejaVu.
Removing it does not break PDF generation; the report simply falls back to a
system font and stops matching the screen.
