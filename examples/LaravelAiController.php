<?php

// Integration example: adapt namespace, routes and services config to your project.
// Route behind Laravel's session + CSRF middleware, with a throttle. Guests allowed.
namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;

class LaravelAiController extends Controller
{
    public function chat(Request $request)
    {
        $input = $request->validate([
            'message' => ['required', 'string', 'max:300'],
            'locale' => ['sometimes', 'in:ar,en'],
            'current_store_id' => ['nullable', 'string', 'max:64'],
        ]);

        $customerId = $request->user() ? (string) $request->user()->getAuthIdentifier() : null;
        $subject = $request->session()->get('ai_subject');
        if (!$subject) {
            $subject = Str::random(48);
            $request->session()->put('ai_subject', $subject);
        }
        if ($request->session()->get('ai_customer') !== $customerId) {
            // A new identity must never inherit an AI session belonging to another identity.
            $request->session()->forget('ai_session');
            $request->session()->put('ai_customer', $customerId);
        }

        $headers = ['X-Session-Subject' => $subject];
        if ($customerId !== null) {
            $headers['X-Customer-ID'] = $customerId;
        }
        $payload = $input + ['session_id' => $request->session()->get('ai_session')];
        $client = Http::withToken(config('services.loulou_ai.token'))
            ->withHeaders($headers)->acceptJson()->connectTimeout(3)->timeout(30);
        $url = rtrim(config('services.loulou_ai.url'), '/') . '/v1/chat';

        try {
            $response = $client->post($url, $payload);
            if ($response->status() === 404 && $payload['session_id'] !== null) {
                $request->session()->forget('ai_session');
                $payload['session_id'] = null;
                $response = $client->post($url, $payload);
            }
        } catch (\Illuminate\Http\Client\ConnectionException $exception) {
            return response()->json(['error' => 'assistant_unavailable'], 503);
        }
        if ($response->successful()) {
            $request->session()->put('ai_session', $response->json('session_id'));
        }
        return response()->json($response->json(), $response->status())
            ->header('Cache-Control', 'no-store');
    }

    // Call BEFORE logout/session invalidation while the old identity is still available.
    public function end(Request $request)
    {
        $id = $request->session()->get('ai_session');
        if ($id) {
            $headers = ['X-Session-Subject' => $request->session()->get('ai_subject')];
            if ($request->user()) {
                $headers['X-Customer-ID'] = (string) $request->user()->getAuthIdentifier();
            }
            try {
                Http::withToken(config('services.loulou_ai.token'))->withHeaders($headers)
                    ->connectTimeout(3)->timeout(5)
                    ->delete(rtrim(config('services.loulou_ai.url'), '/') . '/v1/sessions/' . $id);
            } catch (\Illuminate\Http\Client\ConnectionException $exception) {
                // The ephemeral store TTL also expires abandoned sessions.
            }
        }
        $request->session()->forget(['ai_session', 'ai_subject', 'ai_customer']);
        return response()->noContent();
    }
}
