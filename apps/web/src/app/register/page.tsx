"use client";
import { FormErrorMessage } from "@/components/form-error-message";
import { FriendlyErrorMessage } from "@/components/friendly-error";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { toast } from "sonner";
import { z } from "zod";
import {
  CircleAlertIcon,
  KeyRoundIcon,
  MailIcon,
  SigmaIcon,
  UserIcon,
} from "lucide-react";

import { PageLoader } from "@/components/loading";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { isApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";

const registerSchema = z
  .object({
    display_name: z.string().trim().max(80, "validation.nameLength").optional(),
    email: z.email("validation.email"),
    password: z.string().min(8, "validation.passwordLength"),
    confirm: z.string().min(1, "validation.confirmPassword"),
  })
  .refine((values) => values.password === values.confirm, {
    path: ["confirm"],
    message: "validation.passwordMismatch",
  });

type RegisterValues = z.infer<typeof registerSchema>;

export default function RegisterPage() {
  const router = useRouter();
  const {
    user,
    isInitialized,
    fetchUser,
    register: registerUser,
  } = useAuthStore();
  const [serverError, setServerError] = useState<unknown | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({
    resolver: standardSchemaResolver(registerSchema),
    defaultValues: { display_name: "", email: "", password: "", confirm: "" },
  });

  useEffect(() => {
    if (!isInitialized) void fetchUser();
  }, [isInitialized, fetchUser]);

  useEffect(() => {
    if (isInitialized && user) router.replace("/dashboard");
  }, [isInitialized, user, router]);

  if (!isInitialized || user) {
    return (
      <UiAttributes messages={{ label: { id: "ui.auth.checkingSession" } }}>
        <PageLoader label={"Checking session…"} />
      </UiAttributes>
    );
  }

  async function onSubmit(values: RegisterValues) {
    setServerError(null);
    try {
      const created = await registerUser(
        values.email,
        values.password,
        values.display_name || undefined,
      );
      toast.success(<UiMessage id="ui.auth.accountCreated" />, {
        description: (
          <UiMessage
            id="ui.auth.welcomeToTheBenchValue"
            values={{ value0: String(created.display_name ?? created.email) }}
          />
        ),
      });
      router.replace("/dashboard");
    } catch (error) {
      // Route field-level failures to the right input; the rest go to the alert.
      if (isApiError(error)) {
        if (error.errorCode === "AUTH_EMAIL_EXISTS") {
          setError("email", { type: "server", message: error.errorCode });
          return;
        }
        if (error.errorCode === "AUTH_WEAK_PASSWORD") {
          setError("password", { type: "server", message: error.errorCode });
          return;
        }
      }
      setServerError(error);
    }
  }

  const fieldError = (name: keyof RegisterValues) =>
    errors[name] ? (
      <p id={`register-${name}-error`} className="text-xs text-destructive">
        <FormErrorMessage code={errors[name]?.message} />
      </p>
    ) : null;

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Left — bench notes */}
      <div className="relative hidden flex-col justify-between overflow-hidden border-r p-12 lg:flex">
        <div className="animate-rise-lg space-y-6 pt-16">
          <p className="font-mono text-xs tracking-[0.25em] text-primary uppercase">
            {" "}
            <UiMessage id="ui.auth.newResearcherBenchRegistration" />{" "}
          </p>
          <h1 className="font-heading text-4xl leading-tight font-bold tracking-tight xl:text-5xl">
            {" "}
            <UiMessage id="ui.auth.oneAccount" /> <br />{" "}
            <UiMessage id="ui.auth.everyAnalysis" />{" "}
            <span className="relative inline-block">
              {" "}
              <UiMessage id="ui.auth.onTheRecord" />{" "}
              <span
                aria-hidden
                className="absolute inset-x-0 -bottom-1 h-1.5 rounded-full bg-accent/70"
              />
            </span>
            .
          </h1>
          <p className="max-w-md text-muted-foreground">
            {" "}
            <UiMessage id="ui.auth.projectsDatasetsAndRevisionsAreTiedToYourIdentitySoEveryResultYouPublishCanBeTracedBackToItsSource" />{" "}
          </p>
        </div>

        <ul
          className="animate-rise-lg space-y-2 pb-16 font-mono text-xs text-muted-foreground"
          style={{ animationDelay: "120ms" }}
        >
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.freeOpenSourceMitLicensed" />{" "}
          </li>
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.uploadCsvXlsxParquetUpTo100Mb" />{" "}
          </li>
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.passwordsHashedServerSideNeverStoredInPlainText" />{" "}
          </li>
        </ul>
      </div>

      {/* Right — form */}
      <div className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="animate-rise w-full max-w-md">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <span className="flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-plate">
              <SigmaIcon className="size-5" />
            </span>
            <span className="font-heading text-xl font-bold tracking-tight">
              {" "}
              <UiMessage id="ui.auth.stat" />
              <span className="text-primary">Forge</span>
            </span>
          </div>

          <div className="overflow-hidden rounded-lg border bg-card shadow-plate">
            <div
              aria-hidden
              className="h-1 bg-gradient-to-r from-primary to-accent"
            />
            <div className="p-6 sm:p-8">
              <h2 className="font-heading text-2xl font-bold tracking-tight">
                {" "}
                <UiMessage id="ui.auth.createYourAccount" />{" "}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.auth.alreadyRegistered" />{" "}
                <Link
                  href="/login"
                  className="font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  {" "}
                  <UiMessage id="ui.auth.signInInstead" />{" "}
                </Link>
              </p>

              {serverError !== null && (
                <Alert variant="destructive" className="mt-5">
                  <CircleAlertIcon />
                  <AlertTitle>
                    <FriendlyErrorMessage error={serverError} part="title" />
                  </AlertTitle>
                  <AlertDescription>
                    <FriendlyErrorMessage error={serverError} />
                  </AlertDescription>
                </Alert>
              )}

              <form
                onSubmit={handleSubmit(onSubmit)}
                className="mt-6 space-y-4"
                noValidate
              >
                <div className="space-y-2">
                  <Label htmlFor="register-name">
                    {" "}
                    <UiMessage id="ui.auth.displayName" />{" "}
                    <span className="font-normal text-muted-foreground">
                      {" "}
                      <UiMessage id="ui.projects.optional" />{" "}
                    </span>
                  </Label>
                  <div className="relative">
                    <UserIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="register-name"
                      autoComplete="name"
                      placeholder="Ada Lovelace"
                      className="pl-8"
                      aria-invalid={Boolean(errors.display_name)}
                      {...register("display_name")}
                    />
                  </div>
                  {fieldError("display_name")}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="register-email">
                    <UiMessage id="ui.auth.email" />
                  </Label>
                  <div className="relative">
                    <MailIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="register-email"
                      type="email"
                      autoComplete="email"
                      placeholder="you@lab.org"
                      className="pl-8"
                      aria-invalid={Boolean(errors.email)}
                      aria-describedby={
                        errors.email ? "register-email-error" : undefined
                      }
                      {...register("email")}
                    />
                  </div>
                  {fieldError("email")}
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="register-password">
                      <UiMessage id="ui.auth.password" />
                    </Label>
                    <div className="relative">
                      <KeyRoundIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                      <UiAttributes
                        messages={{ placeholder: { id: "ui.auth.min8Chars" } }}
                      >
                        <Input
                          placeholder={"Min. 8 chars"}
                          id="register-password"
                          type="password"
                          autoComplete="new-password"

                          className="pl-8"
                          aria-invalid={Boolean(errors.password)}
                          aria-describedby={
                            errors.password
                              ? "register-password-error"
                              : undefined
                          }
                          {...register("password")}
                        />
                      </UiAttributes>
                    </div>
                    {fieldError("password")}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="register-confirm">
                      <UiMessage id="ui.auth.confirmPassword" />
                    </Label>
                    <div className="relative">
                      <KeyRoundIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                      <UiAttributes
                        messages={{ placeholder: { id: "ui.auth.repeatIt" } }}
                      >
                        <Input
                          placeholder={"Repeat it"}
                          id="register-confirm"
                          type="password"
                          autoComplete="new-password"

                          className="pl-8"
                          aria-invalid={Boolean(errors.confirm)}
                          aria-describedby={
                            errors.confirm
                              ? "register-confirm-error"
                              : undefined
                          }
                          {...register("confirm")}
                        />
                      </UiAttributes>
                    </div>
                    {fieldError("confirm")}
                  </div>
                </div>

                <Button
                  type="submit"
                  className="mt-2 w-full"
                  size="lg"
                  disabled={isSubmitting}
                >
                  {isSubmitting && <Spinner className="size-4" />}
                  {isSubmitting ? (
                    <UiMessage id="ui.common.forgingAccount" />
                  ) : (
                    <UiMessage id="ui.common.createAccount" />
                  )}
                </Button>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
