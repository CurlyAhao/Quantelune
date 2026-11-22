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
} from "lucide-react";

import { PageLoader } from "@/components/loading";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { useAuthStore } from "@/stores/auth-store";

const loginSchema = z.object({
  email: z.email("validation.email"),
  password: z.string().min(1, "validation.passwordRequired"),
});

type LoginValues = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const router = useRouter();
  const { user, isInitialized, fetchUser, login } = useAuthStore();
  const [serverError, setServerError] = useState<unknown | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: standardSchemaResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  useEffect(() => {
    if (!isInitialized) void fetchUser();
  }, [isInitialized, fetchUser]);

  // Already signed in → straight to the dashboard.
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

  async function onSubmit(values: LoginValues) {
    setServerError(null);
    try {
      const loggedIn = await login(values.email, values.password);
      toast.success(
        <UiMessage
          id="ui.auth.welcomeBackValue"
          values={{ value0: loggedIn.display_name ?? loggedIn.email }}
        />,
      );
    } catch (error) {
      setServerError(error);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Left — bench notes */}
      <div className="relative hidden flex-col justify-between overflow-hidden border-r p-12 lg:flex">
        <div className="animate-rise-lg space-y-6 pt-16">
          <p className="font-mono text-xs tracking-[0.25em] text-primary uppercase">
            {" "}
            <UiMessage id="ui.auth.restrictedBenchCredentialsRequired" />{" "}
          </p>
          <h1 className="font-heading text-4xl leading-tight font-bold tracking-tight xl:text-5xl">
            {" "}
            <UiMessage id="ui.auth.pickUpWhere" /> <br />{" "}
            <UiMessage id="ui.auth.yourData" />{" "}
            <span className="relative inline-block">
              {" "}
              <UiMessage id="ui.auth.leftOff" />{" "}
              <span
                aria-hidden
                className="absolute inset-x-0 -bottom-1 h-1.5 rounded-full bg-accent/70"
              />
            </span>
            .
          </h1>
          <p className="max-w-md text-muted-foreground">
            {" "}
            <UiMessage id="ui.auth.statforgeKeepsEveryUploadProfileAndModelRunOnTheRecordSignInToReturnToYourProjects" />{" "}
          </p>
        </div>

        <ul
          className="animate-rise-lg space-y-2 pb-16 font-mono text-xs text-muted-foreground"
          style={{ animationDelay: "120ms" }}
        >
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.httponlyCookieSessionsNoTokensInLocalstorage" />{" "}
          </li>
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.silentTokenRefreshKeepsLongAnalysesAlive" />{" "}
          </li>
          <li className="flex items-center gap-2">
            <span className="size-1 rounded-full bg-primary" />{" "}
            <UiMessage id="ui.auth.aiContextLeavesOnlyAfterYourExplicitApprovalRawRowsStayLocalByDefault" />{" "}
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
                <UiMessage id="ui.auth.signIn" />{" "}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.auth.newToTheWorkbench" />{" "}
                <Link
                  href="/register"
                  className="font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  {" "}
                  <UiMessage id="ui.auth.createAnAccount" />{" "}
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
                className="mt-6 space-y-5"
                noValidate
              >
                <div className="space-y-2">
                  <Label htmlFor="login-email">
                    <UiMessage id="ui.auth.email" />
                  </Label>
                  <div className="relative">
                    <MailIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="login-email"
                      type="email"
                      autoComplete="email"
                      placeholder="you@lab.org"
                      className="pl-8"
                      aria-invalid={Boolean(errors.email)}
                      aria-describedby={
                        errors.email ? "login-email-error" : undefined
                      }
                      {...register("email")}
                    />
                  </div>
                  {errors.email && (
                    <p
                      id="login-email-error"
                      className="text-xs text-destructive"
                    >
                      <FormErrorMessage code={errors.email.message} />
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="login-password">
                    <UiMessage id="ui.auth.password" />
                  </Label>
                  <div className="relative">
                    <KeyRoundIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="login-password"
                      type="password"
                      autoComplete="current-password"
                      placeholder="••••••••"
                      className="pl-8"
                      aria-invalid={Boolean(errors.password)}
                      aria-describedby={
                        errors.password ? "login-password-error" : undefined
                      }
                      {...register("password")}
                    />
                  </div>
                  {errors.password && (
                    <p
                      id="login-password-error"
                      className="text-xs text-destructive"
                    >
                      <FormErrorMessage code={errors.password.message} />
                    </p>
                  )}
                </div>

                <Button
                  type="submit"
                  className="w-full"
                  size="lg"
                  disabled={isSubmitting}
                >
                  {isSubmitting && <Spinner className="size-4" />}
                  {isSubmitting ? (
                    <UiMessage id="ui.common.signingIn" />
                  ) : (
                    <UiMessage id="ui.auth.signIn" />
                  )}
                </Button>
              </form>
            </div>
          </div>

          <p className="mt-4 text-center font-mono text-[0.65rem] tracking-wide text-muted-foreground uppercase">
            {" "}
            <UiMessage id="ui.auth.sessionsRotateAutomaticallyCookiesAreHttponly" />{" "}
          </p>
        </div>
      </div>
    </div>
  );
}
