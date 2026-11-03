using System.Data.SqlClient;
using System.Net.Http;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace Shop.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    private static readonly HttpClient Inventory = new HttpClient();
    private readonly SqlConnection _db;

    public OrdersController(SqlConnection db)
    {
        _db = db;
    }

    // [HttpPut("{id}")] is a comment and must not be listed.

    [HttpGet("{id}")]
    public IActionResult Get(string id)
    {
        var cmd = new SqlCommand("SELECT * FROM Orders WHERE Id = '" + id + "'", _db);
        return Ok(cmd.ExecuteScalar());
    }

    [HttpPost]
    public IActionResult Create([FromBody] Order order)
    {
        return Created("", order);
    }

    [HttpDelete("{id}")]
    [Authorize(Roles = "Admin")]
    public IActionResult Delete(string id)
    {
        return NoContent();
    }
}

public record Order(string Sku);
