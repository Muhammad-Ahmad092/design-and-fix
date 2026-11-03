<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

class OrderController extends Controller
{
    public function show(string $id)
    {
        return DB::select("SELECT * FROM orders WHERE id = " . $id);
    }

    public function store(Request $request)
    {
        $ch = curl_init('https://inventory.internal/reserve');
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        $body = curl_exec($ch);
        return response()->json(['reserved' => $body !== false], 201);
    }

    public function destroy(string $id)
    {
        DB::delete('DELETE FROM orders WHERE id = ?', [$id]);
        return response()->noContent();
    }
}
