using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpPost]
    public IActionResult Create() => Ok();

    [HttpPost("feedback")]
    [AllowAnonymous]
    public IActionResult Feedback() => Ok();
}
