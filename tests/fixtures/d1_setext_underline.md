# Python 3.11 Release Notes

Version 3.11 introduces significant performance improvements, with speedups of
up to 15-20% for most code. The new exception groups feature provides better
error handling for concurrent code.

===============================
Breaking Changes
==============================

- Async generators now need to handle a new exception type
- Error messages have been reorganized for clarity

The standout feature is the improved error messages, which now show more
context about where the error occurred, making debugging faster.