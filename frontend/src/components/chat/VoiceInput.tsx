/**
 * Bilingual browser voice input.
 *
 * - EN mode -> en-US recognition
 * - Sinhala mode -> si-LK recognition
 * - One click starts listening.
 * - Second click stops listening.
 * - Speech becomes ordinary text inside the existing message input.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

type VoiceLanguage =
  | "en-US"
  | "si-LK";

interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]:
    | SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  [index: number]:
    | SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike
  extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionErrorEventLike
  extends Event {
  readonly error: string;
  readonly message?: string;
}

interface SpeechRecognitionInstanceLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;

  start(): void;
  stop(): void;
  abort(): void;

  onstart:
    | (() => void)
    | null;

  onend:
    | (() => void)
    | null;

  onresult:
    | ((
        event: SpeechRecognitionEventLike
      ) => void)
    | null;

  onerror:
    | ((
        event: SpeechRecognitionErrorEventLike
      ) => void)
    | null;
}

interface SpeechRecognitionConstructorLike {
  new (): SpeechRecognitionInstanceLike;
}

interface SpeechRecognitionWindowLike
  extends Window {
  SpeechRecognition?:
    | SpeechRecognitionConstructorLike;

  webkitSpeechRecognition?:
    | SpeechRecognitionConstructorLike;
}

interface VoiceInputProps {
  value: string;
  onChange: (
    value: string
  ) => void;
  disabled?: boolean;
}

function getRecognitionConstructor():
  | SpeechRecognitionConstructorLike
  | null {
  const speechWindow =
    window as SpeechRecognitionWindowLike;

  return (
    speechWindow.SpeechRecognition ??
    speechWindow.webkitSpeechRecognition ??
    null
  );
}

function containsSinhala(
  text: string
): boolean {
  return /[\u0D80-\u0DFF]/.test(
    text
  );
}

export function VoiceInput({
  value,
  onChange,
  disabled = false,
}: VoiceInputProps) {
  const defaultLanguage: VoiceLanguage =
    navigator.language
      ?.toLowerCase()
      .startsWith("si")
      ? "si-LK"
      : "en-US";

  const [
    language,
    setLanguage,
  ] = useState<VoiceLanguage>(
    defaultLanguage
  );

  const [
    isListening,
    setIsListening,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );

  const recognitionRef =
    useRef<SpeechRecognitionInstanceLike | null>(
      null
    );

  const listeningRef =
    useRef(false);

  const restartRef =
    useRef(false);

  const baseTextRef =
    useRef("");

  const finalTranscriptRef =
    useRef("");

  const supportedRef =
    useRef(true);

  /*
   * If the user has already typed/pasted Sinhala before pressing
   * the microphone, automatically use Sinhala recognition.
   */
  const effectiveLanguage: VoiceLanguage =
    containsSinhala(value)
      ? "si-LK"
      : language;

  const createRecognition =
    useCallback(() => {
      const Recognition =
        getRecognitionConstructor();

      if (!Recognition) {
        supportedRef.current = false;

        setError(
          "Voice input is not supported by this browser."
        );

        return null;
      }

      const recognition =
        new Recognition();

      recognition.lang =
        effectiveLanguage;

      recognition.continuous =
        true;

      recognition.interimResults =
        true;

      recognition.maxAlternatives =
        1;

      recognition.onstart = () => {
        listeningRef.current =
          true;

        setIsListening(true);
        setError(null);
      };

      recognition.onresult = (
        event
      ) => {
        let interimTranscript =
          "";

        /*
         * The browser may replace interim results.
         * Only append FINAL results permanently.
         */
        for (
          let index =
            event.resultIndex;
          index <
          event.results.length;
          index += 1
        ) {
          const result =
            event.results[index];

          if (
            !result ||
            result.length === 0
          ) {
            continue;
          }

          const transcript =
            result[0]
              .transcript
              .trim();

          if (!transcript) {
            continue;
          }

          if (result.isFinal) {
            finalTranscriptRef.current =
              `${finalTranscriptRef.current} ${transcript}`
                .replace(
                  /\s+/g,
                  " "
                )
                .trim();
          } else {
            interimTranscript =
              `${interimTranscript} ${transcript}`
                .replace(
                  /\s+/g,
                  " "
                )
                .trim();
          }
        }

        const baseText =
          baseTextRef.current.trim();

        const finalText =
          finalTranscriptRef.current.trim();

        const combined =
          [
            baseText,
            finalText,
            interimTranscript,
          ]
            .filter(Boolean)
            .join(" ")
            .replace(
              /\s+/g,
              " "
            )
            .trim();

        if (combined) {
          onChange(
            combined
          );
        }
      };

      recognition.onerror = (
        event
      ) => {
        const nonFatalErrors =
          new Set([
            "no-speech",
            "aborted",
          ]);

        if (
          nonFatalErrors.has(
            event.error
          )
        ) {
          return;
        }

        listeningRef.current =
          false;

        restartRef.current =
          false;

        setIsListening(false);

        if (
          event.error ===
            "not-allowed" ||
          event.error ===
            "service-not-allowed"
        ) {
          setError(
            "Microphone access was denied. Allow microphone access in your browser settings."
          );
          return;
        }

        if (
          event.error ===
          "audio-capture"
        ) {
          setError(
            "No microphone was available."
          );
          return;
        }

        if (
          event.error ===
          "network"
        ) {
          setError(
            "Voice recognition needs a network connection."
          );
          return;
        }

        setError(
          "Voice recognition stopped because of an unexpected error."
        );
      };

      recognition.onend =
        () => {
          /*
           * Some browsers automatically end a recognition session
           * after pauses. Continue listening until the user explicitly
           * clicks the microphone again.
           */
          if (
            restartRef.current &&
            listeningRef.current &&
            !disabled
          ) {
            try {
              recognition.start();
              return;
            } catch {
              // Browser may already be transitioning.
            }
          }

          listeningRef.current =
            false;

          setIsListening(false);
        };

      return recognition;
    }, [
      disabled,
      effectiveLanguage,
      onChange,
    ]);

  const startListening =
    useCallback(() => {
      if (disabled) {
        return;
      }

      const Recognition =
        getRecognitionConstructor();

      if (!Recognition) {
        supportedRef.current =
          false;

        setError(
          "Voice input is not supported by this browser."
        );

        return;
      }

      try {
        if (
          recognitionRef.current
        ) {
          try {
            recognitionRef.current.abort();
          } catch {
            // Ignore cleanup error.
          }
        }

        baseTextRef.current =
          value.trim();

        finalTranscriptRef.current =
          "";

        restartRef.current =
          true;

        setError(null);

        const recognition =
          createRecognition();

        if (!recognition) {
          restartRef.current =
            false;

          return;
        }

        recognitionRef.current =
          recognition;

        recognition.start();

      } catch (
        exception
      ) {
        restartRef.current =
          false;

        listeningRef.current =
          false;

        setIsListening(false);

        setError(
          exception instanceof
            Error
            ? exception.message
            : "Unable to start voice recognition."
        );
      }
    }, [
      createRecognition,
      disabled,
      value,
    ]);

  const stopListening =
    useCallback(() => {
      restartRef.current =
        false;

      listeningRef.current =
        false;

      try {
        recognitionRef.current?.stop();
      } catch {
        try {
          recognitionRef.current?.abort();
        } catch {
          // Ignore cleanup errors.
        }
      }

      setIsListening(false);
    }, []);

  const toggleListening =
    useCallback(() => {
      if (disabled) {
        return;
      }

      if (isListening) {
        stopListening();
      } else {
        startListening();
      }
    }, [
      disabled,
      isListening,
      startListening,
      stopListening,
    ]);

  const toggleLanguage =
    useCallback(() => {
      if (
        disabled ||
        isListening
      ) {
        return;
      }

      setLanguage(
        (
          previous
        ) =>
          previous ===
          "en-US"
            ? "si-LK"
            : "en-US"
      );

      setError(null);
    }, [
      disabled,
      isListening,
    ]);

  useEffect(() => {
    if (
      disabled &&
      isListening
    ) {
      stopListening();
    }
  }, [
    disabled,
    isListening,
    stopListening,
  ]);

  useEffect(() => {
    return () => {
      restartRef.current =
        false;

      listeningRef.current =
        false;

      try {
        recognitionRef.current?.abort();
      } catch {
        // Ignore cleanup errors.
      }

      recognitionRef.current =
        null;
    };
  }, []);

  const isSinhala =
    effectiveLanguage ===
    "si-LK";

  return (
    <div className="voice-input-wrapper">
      <button
        type="button"
        className="voice-language-button"
        onClick={
          toggleLanguage
        }
        disabled={
          disabled ||
          isListening
        }
        aria-label={
          isSinhala
            ? "Voice language Sinhala"
            : "Voice language English"
        }
        title="Change voice language"
      >
        {isSinhala
          ? "සිං"
          : "EN"}
      </button>

      <button
        type="button"
        className={
          isListening
            ? "voice-input-button listening"
            : "voice-input-button"
        }
        onClick={
          toggleListening
        }
        disabled={disabled}
        aria-label={
          isListening
            ? "Stop voice input"
            : "Start voice input"
        }
        aria-pressed={
          isListening
        }
        title={
          isListening
            ? "Stop listening"
            : `Speak in ${
                isSinhala
                  ? "Sinhala"
                  : "English"
              }`
        }
      >
        <svg
          className="voice-input-icon"
          viewBox="0 0 24 24"
          aria-hidden="true"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect
            x="9"
            y="2"
            width="6"
            height="12"
            rx="3"
          />
          <path d="M5 10a7 7 0 0 0 14 0" />
          <path d="M12 19v3" />
          <path d="M8 22h8" />
        </svg>
      </button>

      {isListening && (
        <span
          className="voice-input-status"
          aria-live="polite"
        >
          {isSinhala
            ? "සවන් දෙමින්…"
            : "Listening…"}
        </span>
      )}

      {error && (
        <span
          className="voice-input-error"
          role="status"
        >
          {error}
        </span>
      )}
    </div>
  );
}