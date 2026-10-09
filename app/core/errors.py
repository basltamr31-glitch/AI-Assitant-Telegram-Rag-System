"""The failures this service knows how to name.

Why typed errors
----------------
Before Phase 13, every failure below the responder was an `Exception`, and
the responder had one answer for all of them: "The model did not answer."
Measured with Qdrant stopped, that was wrong twice over - the model was fine,
the knowledge base was not - and it arrived after 82 seconds, past n8n's
timeout, so in Telegram the user saw nothing at all.

A failure that has a name can be handled by name: the right message, the
right decision about retrying, and an honest log line. Each class says two
things about itself:

* `retryable` - would trying again soon plausibly work? An overloaded
  provider, yes. A day's quota spent, no: retrying only spends the time the
  user is waiting for an answer.
* `user_message` - what the person in the chat should be told. In Arabic,
  plain, and never containing a stack trace.

Anything that is not one of these is still caught by the responder - as a
bug, with a trace id the user can quote.
"""

from __future__ import annotations


class ServiceError(Exception):
    """A dependency failed in a way we recognise."""

    retryable = False
    user_message = "⚠️ <b>حدث خطأ غير متوقع.</b>"


class KnowledgeBaseUnavailable(ServiceError):
    """Qdrant cannot be reached, or is failing."""

    user_message = (
        "⚠️ <b>قاعدة المعرفة غير متاحة الآن.</b>\n\n"
        "لا أستطيع الوصول إلى مستنداتك، ولن أجيب من معرفتي العامة بدلاً منها. "
        "الأوامر والتحية ما زالت تعمل."
    )


class ModelUnavailable(ServiceError):
    """The model provider is overloaded, failing, or too slow - for now."""

    retryable = True
    user_message = (
        "⏳ <b>خدمة النموذج مزدحمة الآن.</b>\n\n"
        "حاولت أكثر من مرة ولم تنجح. أعد السؤال بعد دقيقة."
    )


class QuotaExhausted(ServiceError):
    """The free tier's daily request allowance is spent."""

    user_message = (
        "🔋 <b>انتهت حصة اليوم من طلبات النموذج.</b>\n\n"
        "تتجدّد الساعة 3:00 فجراً بتوقيت دمشق (منتصف الليل UTC). "
        "الأوامر ما زالت تعمل حتى ذلك الحين."
    )


class ModelRejected(ServiceError):
    """The provider refused the request: a bad key, no credit, unknown model.

    A configuration problem, not a transient one, so never retried.
    """

    user_message = (
        "⚠️ <b>رفض مزوّد النموذج الطلب.</b>\n\n"
        "هذه مشكلة في الإعدادات (المفتاح أو اسم النموذج)، وتفاصيلها في سجلّ الخادم."
    )
